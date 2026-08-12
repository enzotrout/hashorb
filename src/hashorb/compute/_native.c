#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define HEADER_PREFIX_LENGTH 76
#define HEADER_LENGTH 80
#define TARGET_LENGTH 32
#define DIGEST_LENGTH 32
#define NONCE_LIMIT UINT64_C(4294967296)

typedef struct {
    uint8_t data[64];
    uint32_t state[8];
    uint64_t bit_length;
    size_t data_length;
} Sha256Context;

static const uint32_t SHA256_CONSTANTS[64] = {
    UINT32_C(0x428a2f98), UINT32_C(0x71374491), UINT32_C(0xb5c0fbcf),
    UINT32_C(0xe9b5dba5), UINT32_C(0x3956c25b), UINT32_C(0x59f111f1),
    UINT32_C(0x923f82a4), UINT32_C(0xab1c5ed5), UINT32_C(0xd807aa98),
    UINT32_C(0x12835b01), UINT32_C(0x243185be), UINT32_C(0x550c7dc3),
    UINT32_C(0x72be5d74), UINT32_C(0x80deb1fe), UINT32_C(0x9bdc06a7),
    UINT32_C(0xc19bf174), UINT32_C(0xe49b69c1), UINT32_C(0xefbe4786),
    UINT32_C(0x0fc19dc6), UINT32_C(0x240ca1cc), UINT32_C(0x2de92c6f),
    UINT32_C(0x4a7484aa), UINT32_C(0x5cb0a9dc), UINT32_C(0x76f988da),
    UINT32_C(0x983e5152), UINT32_C(0xa831c66d), UINT32_C(0xb00327c8),
    UINT32_C(0xbf597fc7), UINT32_C(0xc6e00bf3), UINT32_C(0xd5a79147),
    UINT32_C(0x06ca6351), UINT32_C(0x14292967), UINT32_C(0x27b70a85),
    UINT32_C(0x2e1b2138), UINT32_C(0x4d2c6dfc), UINT32_C(0x53380d13),
    UINT32_C(0x650a7354), UINT32_C(0x766a0abb), UINT32_C(0x81c2c92e),
    UINT32_C(0x92722c85), UINT32_C(0xa2bfe8a1), UINT32_C(0xa81a664b),
    UINT32_C(0xc24b8b70), UINT32_C(0xc76c51a3), UINT32_C(0xd192e819),
    UINT32_C(0xd6990624), UINT32_C(0xf40e3585), UINT32_C(0x106aa070),
    UINT32_C(0x19a4c116), UINT32_C(0x1e376c08), UINT32_C(0x2748774c),
    UINT32_C(0x34b0bcb5), UINT32_C(0x391c0cb3), UINT32_C(0x4ed8aa4a),
    UINT32_C(0x5b9cca4f), UINT32_C(0x682e6ff3), UINT32_C(0x748f82ee),
    UINT32_C(0x78a5636f), UINT32_C(0x84c87814), UINT32_C(0x8cc70208),
    UINT32_C(0x90befffa), UINT32_C(0xa4506ceb), UINT32_C(0xbef9a3f7),
    UINT32_C(0xc67178f2),
};

static uint32_t rotate_right(uint32_t value, unsigned int count) {
    return (value >> count) | (value << (32U - count));
}

static void sha256_transform(Sha256Context *context, const uint8_t block[64]) {
    uint32_t schedule[64];
    uint32_t a;
    uint32_t b;
    uint32_t c;
    uint32_t d;
    uint32_t e;
    uint32_t f;
    uint32_t g;
    uint32_t h;

    for (size_t index = 0; index < 16; ++index) {
        const size_t offset = index * 4;
        schedule[index] = ((uint32_t)block[offset] << 24U) |
                          ((uint32_t)block[offset + 1] << 16U) |
                          ((uint32_t)block[offset + 2] << 8U) |
                          (uint32_t)block[offset + 3];
    }
    for (size_t index = 16; index < 64; ++index) {
        const uint32_t sigma_zero = rotate_right(schedule[index - 15], 7U) ^
                                    rotate_right(schedule[index - 15], 18U) ^
                                    (schedule[index - 15] >> 3U);
        const uint32_t sigma_one = rotate_right(schedule[index - 2], 17U) ^
                                   rotate_right(schedule[index - 2], 19U) ^
                                   (schedule[index - 2] >> 10U);
        schedule[index] = schedule[index - 16] + sigma_zero + schedule[index - 7] + sigma_one;
    }

    a = context->state[0];
    b = context->state[1];
    c = context->state[2];
    d = context->state[3];
    e = context->state[4];
    f = context->state[5];
    g = context->state[6];
    h = context->state[7];

    for (size_t index = 0; index < 64; ++index) {
        const uint32_t choice = (e & f) ^ ((~e) & g);
        const uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
        const uint32_t sum_zero = rotate_right(a, 2U) ^ rotate_right(a, 13U) ^
                                  rotate_right(a, 22U);
        const uint32_t sum_one = rotate_right(e, 6U) ^ rotate_right(e, 11U) ^
                                 rotate_right(e, 25U);
        const uint32_t temporary_one = h + sum_one + choice + SHA256_CONSTANTS[index] +
                                       schedule[index];
        const uint32_t temporary_two = sum_zero + majority;

        h = g;
        g = f;
        f = e;
        e = d + temporary_one;
        d = c;
        c = b;
        b = a;
        a = temporary_one + temporary_two;
    }

    context->state[0] += a;
    context->state[1] += b;
    context->state[2] += c;
    context->state[3] += d;
    context->state[4] += e;
    context->state[5] += f;
    context->state[6] += g;
    context->state[7] += h;
}

static void sha256_initialize(Sha256Context *context) {
    context->data_length = 0;
    context->bit_length = 0;
    context->state[0] = UINT32_C(0x6a09e667);
    context->state[1] = UINT32_C(0xbb67ae85);
    context->state[2] = UINT32_C(0x3c6ef372);
    context->state[3] = UINT32_C(0xa54ff53a);
    context->state[4] = UINT32_C(0x510e527f);
    context->state[5] = UINT32_C(0x9b05688c);
    context->state[6] = UINT32_C(0x1f83d9ab);
    context->state[7] = UINT32_C(0x5be0cd19);
}

static void sha256_update(Sha256Context *context, const uint8_t *data, size_t length) {
    for (size_t index = 0; index < length; ++index) {
        context->data[context->data_length++] = data[index];
        if (context->data_length == 64) {
            sha256_transform(context, context->data);
            context->bit_length += UINT64_C(512);
            context->data_length = 0;
        }
    }
}

static void sha256_finalize(Sha256Context *context, uint8_t digest[DIGEST_LENGTH]) {
    size_t index = context->data_length;

    context->data[index++] = UINT8_C(0x80);
    if (index > 56) {
        while (index < 64) {
            context->data[index++] = 0;
        }
        sha256_transform(context, context->data);
        index = 0;
    }
    while (index < 56) {
        context->data[index++] = 0;
    }

    context->bit_length += (uint64_t)context->data_length * UINT64_C(8);
    for (size_t byte_index = 0; byte_index < 8; ++byte_index) {
        context->data[63 - byte_index] =
            (uint8_t)(context->bit_length >> (unsigned int)(byte_index * 8));
    }
    sha256_transform(context, context->data);

    for (size_t word = 0; word < 8; ++word) {
        digest[word * 4] = (uint8_t)(context->state[word] >> 24U);
        digest[word * 4 + 1] = (uint8_t)(context->state[word] >> 16U);
        digest[word * 4 + 2] = (uint8_t)(context->state[word] >> 8U);
        digest[word * 4 + 3] = (uint8_t)context->state[word];
    }
}

static void double_sha256(const uint8_t header[HEADER_LENGTH],
                          uint8_t digest[DIGEST_LENGTH]) {
    Sha256Context context;
    uint8_t first_digest[DIGEST_LENGTH];

    sha256_initialize(&context);
    sha256_update(&context, header, HEADER_LENGTH);
    sha256_finalize(&context, first_digest);
    sha256_initialize(&context);
    sha256_update(&context, first_digest, DIGEST_LENGTH);
    sha256_finalize(&context, digest);
}

static int digest_meets_target(const uint8_t digest[DIGEST_LENGTH],
                               const uint8_t target[TARGET_LENGTH]) {
    for (size_t offset = TARGET_LENGTH; offset > 0; --offset) {
        const size_t index = offset - 1;
        if (digest[index] < target[index]) {
            return 1;
        }
        if (digest[index] > target[index]) {
            return 0;
        }
    }
    return 1;
}

static int digest_less_than(const uint8_t left[DIGEST_LENGTH],
                            const uint8_t right[DIGEST_LENGTH]) {
    for (size_t offset = DIGEST_LENGTH; offset > 0; --offset) {
        const size_t index = offset - 1;
        if (left[index] < right[index]) {
            return 1;
        }
        if (left[index] > right[index]) {
            return 0;
        }
    }
    return 0;
}

static int target_is_nonzero(const uint8_t target[TARGET_LENGTH]) {
    uint8_t combined = 0;

    for (size_t index = 0; index < TARGET_LENGTH; ++index) {
        combined |= target[index];
    }
    return combined != 0;
}

static int exact_unsigned_integer(PyObject *value, uint64_t *result) {
    unsigned long long converted;

    if (!PyLong_CheckExact(value)) {
        PyErr_SetString(PyExc_TypeError, "nonce bounds must be integers");
        return 0;
    }
    converted = PyLong_AsUnsignedLongLong(value);
    if (converted == (unsigned long long)-1 && PyErr_Occurred()) {
        return 0;
    }
    *result = (uint64_t)converted;
    return 1;
}

static PyObject *build_search_result(int found, uint64_t nonce, const uint8_t *digest,
                                     int meets_share, int meets_network,
                                     uint64_t hashes_checked, uint64_t best_nonce) {
    PyObject *nonce_value = found ? PyLong_FromUnsignedLongLong(nonce) : Py_NewRef(Py_None);
    PyObject *digest_value =
        found ? PyBytes_FromStringAndSize((const char *)digest, DIGEST_LENGTH) : Py_NewRef(Py_None);
    PyObject *share_value = PyBool_FromLong(meets_share);
    PyObject *network_value = PyBool_FromLong(meets_network);
    PyObject *count_value = PyLong_FromUnsignedLongLong(hashes_checked);
    PyObject *best_nonce_value = PyLong_FromUnsignedLongLong(best_nonce);
    PyObject *result;

    if (nonce_value == NULL || digest_value == NULL || share_value == NULL ||
        network_value == NULL || count_value == NULL || best_nonce_value == NULL) {
        Py_XDECREF(nonce_value);
        Py_XDECREF(digest_value);
        Py_XDECREF(share_value);
        Py_XDECREF(network_value);
        Py_XDECREF(count_value);
        Py_XDECREF(best_nonce_value);
        return NULL;
    }
    result = PyTuple_Pack(6, nonce_value, digest_value, share_value, network_value,
                          count_value, best_nonce_value);
    Py_DECREF(nonce_value);
    Py_DECREF(digest_value);
    Py_DECREF(share_value);
    Py_DECREF(network_value);
    Py_DECREF(count_value);
    Py_DECREF(best_nonce_value);
    return result;
}

static PyObject *search_nonce_range(PyObject *self, PyObject *arguments) {
    PyObject *header_value;
    PyObject *share_target_value;
    PyObject *network_target_value;
    PyObject *start_value;
    PyObject *stop_value;
    char *header_prefix;
    char *share_target;
    char *network_target;
    Py_ssize_t header_length;
    Py_ssize_t share_target_length;
    Py_ssize_t network_target_length;
    uint64_t start_nonce;
    uint64_t stop_nonce;
    uint64_t matched_nonce = 0;
    uint64_t best_nonce = 0;
    uint64_t hashes_checked = 0;
    uint8_t header[HEADER_LENGTH];
    uint8_t digest[DIGEST_LENGTH];
    uint8_t best_digest[DIGEST_LENGTH];
    uint8_t share_target_copy[TARGET_LENGTH];
    uint8_t network_target_copy[TARGET_LENGTH];
    int found = 0;
    int has_best = 0;
    int meets_share = 0;
    int meets_network = 0;

    (void)self;
    if (!PyArg_ParseTuple(arguments, "OOOOO", &header_value, &share_target_value,
                          &network_target_value, &start_value, &stop_value)) {
        return NULL;
    }
    if (!PyBytes_CheckExact(header_value) || !PyBytes_CheckExact(share_target_value) ||
        !PyBytes_CheckExact(network_target_value)) {
        PyErr_SetString(PyExc_TypeError, "native search byte inputs must be bytes");
        return NULL;
    }
    if (PyBytes_AsStringAndSize(header_value, &header_prefix, &header_length) < 0 ||
        PyBytes_AsStringAndSize(share_target_value, &share_target, &share_target_length) < 0 ||
        PyBytes_AsStringAndSize(network_target_value, &network_target,
                                &network_target_length) < 0) {
        return NULL;
    }
    if (header_length != HEADER_PREFIX_LENGTH) {
        PyErr_SetString(PyExc_ValueError, "header prefix must contain exactly 76 bytes");
        return NULL;
    }
    if (share_target_length != TARGET_LENGTH || network_target_length != TARGET_LENGTH) {
        PyErr_SetString(PyExc_ValueError, "targets must contain exactly 32 bytes");
        return NULL;
    }
    if (!exact_unsigned_integer(start_value, &start_nonce) ||
        !exact_unsigned_integer(stop_value, &stop_nonce)) {
        return NULL;
    }
    if (start_nonce >= NONCE_LIMIT || stop_nonce == 0 || stop_nonce > NONCE_LIMIT ||
        start_nonce >= stop_nonce) {
        PyErr_SetString(PyExc_ValueError, "native search nonce range is invalid");
        return NULL;
    }

    memcpy(header, header_prefix, HEADER_PREFIX_LENGTH);
    memcpy(share_target_copy, share_target, TARGET_LENGTH);
    memcpy(network_target_copy, network_target, TARGET_LENGTH);
    if (!target_is_nonzero(share_target_copy) || !target_is_nonzero(network_target_copy)) {
        PyErr_SetString(PyExc_ValueError, "targets must encode positive integers");
        return NULL;
    }

    Py_BEGIN_ALLOW_THREADS
    for (uint64_t nonce = start_nonce; nonce < stop_nonce; ++nonce) {
        header[76] = (uint8_t)nonce;
        header[77] = (uint8_t)(nonce >> 8U);
        header[78] = (uint8_t)(nonce >> 16U);
        header[79] = (uint8_t)(nonce >> 24U);
        double_sha256(header, digest);
        ++hashes_checked;
        if (!has_best || digest_less_than(digest, best_digest)) {
            memcpy(best_digest, digest, DIGEST_LENGTH);
            best_nonce = nonce;
            has_best = 1;
        }
        meets_share = digest_meets_target(digest, share_target_copy);
        meets_network = digest_meets_target(digest, network_target_copy);
        if (meets_share || meets_network) {
            found = 1;
            matched_nonce = nonce;
            break;
        }
    }
    Py_END_ALLOW_THREADS

    return build_search_result(found, matched_nonce, digest, meets_share, meets_network,
                               hashes_checked, best_nonce);
}

PyDoc_STRVAR(search_nonce_range_doc,
             "search_nonce_range(header_prefix, share_target, network_target, start, stop)\n"
             "--\n\n"
             "Search one exact half-open nonce range with raw Bitcoin double-SHA256.");

static PyMethodDef native_methods[] = {
    {"search_nonce_range", search_nonce_range, METH_VARARGS, search_nonce_range_doc},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef native_module = {
    .m_base = PyModuleDef_HEAD_INIT,
    .m_name = "_native",
    .m_doc = "Portable sequential native CPU search implementation.",
    .m_size = -1,
    .m_methods = native_methods,
};

PyMODINIT_FUNC PyInit__native(void) {
    return PyModule_Create(&native_module);
}
