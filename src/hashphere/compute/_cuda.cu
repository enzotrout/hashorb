#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <cuda_runtime.h>
#include <limits.h>
#include <stdint.h>

#define HEADER_PREFIX_LENGTH 76
#define HEADER_LENGTH 80
#define TARGET_LENGTH 32
#define DIGEST_LENGTH 32
#define THREADS_PER_BLOCK 256
#define MAX_BLOCKS 65535
#define NONCE_LIMIT 0x100000000ULL

static int initialized_device = -1;

__device__ __constant__ uint32_t SHA256_CONSTANTS[64] = {
    0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU,
    0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U, 0xd807aa98U, 0x12835b01U,
    0x243185beU, 0x550c7dc3U, 0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U,
    0xc19bf174U, 0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
    0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU, 0x983e5152U,
    0xa831c66dU, 0xb00327c8U, 0xbf597fc7U, 0xc6e00bf3U, 0xd5a79147U,
    0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU,
    0x53380d13U, 0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
    0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U, 0xd192e819U,
    0xd6990624U, 0xf40e3585U, 0x106aa070U, 0x19a4c116U, 0x1e376c08U,
    0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU,
    0x682e6ff3U, 0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
    0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U,
};

__device__ static uint32_t rotate_right(uint32_t value, uint32_t count) {
    return (value >> count) | (value << (32U - count));
}

__device__ static void sha256_transform(uint32_t state[8], const uint8_t block[64]) {
    uint32_t words[64];
    for (int index = 0; index < 16; ++index) {
        int offset = index * 4;
        words[index] = ((uint32_t)block[offset] << 24U) |
                       ((uint32_t)block[offset + 1] << 16U) |
                       ((uint32_t)block[offset + 2] << 8U) |
                       (uint32_t)block[offset + 3];
    }
    for (int index = 16; index < 64; ++index) {
        uint32_t previous_15 = words[index - 15];
        uint32_t previous_2 = words[index - 2];
        uint32_t sigma_0 = rotate_right(previous_15, 7U) ^
                           rotate_right(previous_15, 18U) ^ (previous_15 >> 3U);
        uint32_t sigma_1 = rotate_right(previous_2, 17U) ^
                           rotate_right(previous_2, 19U) ^ (previous_2 >> 10U);
        words[index] = words[index - 16] + sigma_0 + words[index - 7] + sigma_1;
    }

    uint32_t a = state[0];
    uint32_t b = state[1];
    uint32_t c = state[2];
    uint32_t d = state[3];
    uint32_t e = state[4];
    uint32_t f = state[5];
    uint32_t g = state[6];
    uint32_t h = state[7];

    for (int index = 0; index < 64; ++index) {
        uint32_t sum_1 = rotate_right(e, 6U) ^ rotate_right(e, 11U) ^
                         rotate_right(e, 25U);
        uint32_t choice = (e & f) ^ ((~e) & g);
        uint32_t temporary_1 =
            h + sum_1 + choice + SHA256_CONSTANTS[index] + words[index];
        uint32_t sum_0 = rotate_right(a, 2U) ^ rotate_right(a, 13U) ^
                         rotate_right(a, 22U);
        uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
        uint32_t temporary_2 = sum_0 + majority;

        h = g;
        g = f;
        f = e;
        e = d + temporary_1;
        d = c;
        c = b;
        b = a;
        a = temporary_1 + temporary_2;
    }

    state[0] += a;
    state[1] += b;
    state[2] += c;
    state[3] += d;
    state[4] += e;
    state[5] += f;
    state[6] += g;
    state[7] += h;
}

__device__ static void sha256_bytes(const uint8_t *data,
                                    uint32_t length,
                                    uint8_t digest[DIGEST_LENGTH]) {
    uint32_t state[8] = {
        0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
        0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U,
    };
    uint32_t complete_blocks = length / 64U;
    for (uint32_t block_index = 0; block_index < complete_blocks; ++block_index) {
        sha256_transform(state, data + block_index * 64U);
    }

    uint8_t final_block[64] = {0};
    uint32_t remaining = length % 64U;
    for (uint32_t index = 0; index < remaining; ++index) {
        final_block[index] = data[complete_blocks * 64U + index];
    }
    final_block[remaining] = 0x80U;
    if (remaining >= 56U) {
        sha256_transform(state, final_block);
        for (int index = 0; index < 64; ++index) {
            final_block[index] = 0U;
        }
    }

    uint64_t bit_length = (uint64_t)length * 8ULL;
    for (int index = 0; index < 8; ++index) {
        final_block[63 - index] = (uint8_t)(bit_length >> (index * 8));
    }
    sha256_transform(state, final_block);

    for (int index = 0; index < 8; ++index) {
        digest[index * 4] = (uint8_t)(state[index] >> 24U);
        digest[index * 4 + 1] = (uint8_t)(state[index] >> 16U);
        digest[index * 4 + 2] = (uint8_t)(state[index] >> 8U);
        digest[index * 4 + 3] = (uint8_t)state[index];
    }
}

__device__ static void double_sha256_header(const uint8_t header[HEADER_LENGTH],
                                            uint8_t digest[DIGEST_LENGTH]) {
    uint8_t first_digest[DIGEST_LENGTH];
    sha256_bytes(header, HEADER_LENGTH, first_digest);
    sha256_bytes(first_digest, DIGEST_LENGTH, digest);
}

__device__ static bool digest_meets_target(const uint8_t digest[DIGEST_LENGTH],
                                           const uint8_t target[TARGET_LENGTH]) {
    for (int index = DIGEST_LENGTH - 1; index >= 0; --index) {
        if (digest[index] < target[index]) {
            return true;
        }
        if (digest[index] > target[index]) {
            return false;
        }
    }
    return true;
}

__device__ static void evaluate_nonce(const uint8_t *header_prefix,
                                      const uint8_t *share_target,
                                      const uint8_t *network_target,
                                      uint32_t nonce,
                                      bool *meets_share,
                                      bool *meets_network) {
    uint8_t header[HEADER_LENGTH];
    uint8_t digest[DIGEST_LENGTH];
    for (int index = 0; index < HEADER_PREFIX_LENGTH; ++index) {
        header[index] = header_prefix[index];
    }
    header[76] = (uint8_t)nonce;
    header[77] = (uint8_t)(nonce >> 8U);
    header[78] = (uint8_t)(nonce >> 16U);
    header[79] = (uint8_t)(nonce >> 24U);
    double_sha256_header(header, digest);
    *meets_share = digest_meets_target(digest, share_target);
    *meets_network = digest_meets_target(digest, network_target);
}

__global__ static void search_kernel(const uint8_t *header_prefix,
                                     const uint8_t *share_target,
                                     const uint8_t *network_target,
                                     uint64_t start_nonce,
                                     uint64_t range_size,
                                     unsigned long long *candidate_nonce) {
    uint64_t lane = (uint64_t)blockIdx.x * blockDim.x + threadIdx.x;
    uint64_t stride = (uint64_t)gridDim.x * blockDim.x;
    for (uint64_t offset = lane; offset < range_size; offset += stride) {
        uint32_t nonce = (uint32_t)(start_nonce + offset);
        bool meets_share = false;
        bool meets_network = false;
        evaluate_nonce(header_prefix,
                       share_target,
                       network_target,
                       nonce,
                       &meets_share,
                       &meets_network);
        if (meets_share || meets_network) {
            atomicMin(candidate_nonce, (unsigned long long)nonce);
        }
    }
}

__global__ static void verify_candidate_kernel(const uint8_t *header_prefix,
                                               const uint8_t *share_target,
                                               const uint8_t *network_target,
                                               uint32_t nonce,
                                               uint8_t *flags) {
    bool meets_share = false;
    bool meets_network = false;
    evaluate_nonce(header_prefix,
                   share_target,
                   network_target,
                   nonce,
                   &meets_share,
                   &meets_network);
    flags[0] = meets_share ? 1U : 0U;
    flags[1] = meets_network ? 1U : 0U;
}

static void set_cuda_error(void) {
    PyErr_SetString(PyExc_RuntimeError, "CUDA operation failed");
}

static PyObject *cuda_initialize_device(PyObject *self, PyObject *args) {
    int device_ordinal;
    int device_count;
    (void)self;
    if (!PyArg_ParseTuple(args, "i", &device_ordinal)) {
        return NULL;
    }
    if (device_ordinal < 0) {
        PyErr_SetString(PyExc_ValueError, "CUDA device ordinal is invalid");
        return NULL;
    }
    if (cudaGetDeviceCount(&device_count) != cudaSuccess ||
        device_ordinal >= device_count ||
        cudaSetDevice(device_ordinal) != cudaSuccess ||
        cudaFree(NULL) != cudaSuccess) {
        set_cuda_error();
        return NULL;
    }
    initialized_device = device_ordinal;
    Py_RETURN_NONE;
}

static PyObject *build_search_result(unsigned long long candidate,
                                     unsigned char meets_share,
                                     unsigned char meets_network,
                                     unsigned long long hashes_checked) {
    PyObject *result = PyTuple_New(4);
    if (result == NULL) {
        return NULL;
    }
    PyObject *nonce_object =
        candidate == ULLONG_MAX ? Py_NewRef(Py_None) : PyLong_FromUnsignedLongLong(candidate);
    PyObject *share_object = PyBool_FromLong(meets_share != 0U);
    PyObject *network_object = PyBool_FromLong(meets_network != 0U);
    PyObject *count_object = PyLong_FromUnsignedLongLong(hashes_checked);
    if (nonce_object == NULL || share_object == NULL || network_object == NULL ||
        count_object == NULL) {
        Py_XDECREF(nonce_object);
        Py_XDECREF(share_object);
        Py_XDECREF(network_object);
        Py_XDECREF(count_object);
        Py_DECREF(result);
        return NULL;
    }
    PyTuple_SET_ITEM(result, 0, nonce_object);
    PyTuple_SET_ITEM(result, 1, share_object);
    PyTuple_SET_ITEM(result, 2, network_object);
    PyTuple_SET_ITEM(result, 3, count_object);
    return result;
}

static PyObject *cuda_search_nonce_range(PyObject *self, PyObject *args) {
    Py_buffer header_prefix = {0};
    Py_buffer share_target = {0};
    Py_buffer network_target = {0};
    unsigned long long start_nonce;
    unsigned long long stop_nonce;
    uint8_t *device_header = NULL;
    uint8_t *device_share = NULL;
    uint8_t *device_network = NULL;
    unsigned long long *device_candidate = NULL;
    uint8_t *device_flags = NULL;
    unsigned long long candidate = ULLONG_MAX;
    unsigned char flags[2] = {0U, 0U};
    unsigned long long range_size = 0ULL;
    unsigned long long required_blocks = 0ULL;
    int block_count = 0;
    bool failed = false;
    (void)self;

    if (!PyArg_ParseTuple(args,
                          "y*y*y*KK",
                          &header_prefix,
                          &share_target,
                          &network_target,
                          &start_nonce,
                          &stop_nonce)) {
        return NULL;
    }
    if (initialized_device < 0 || header_prefix.len != HEADER_PREFIX_LENGTH ||
        share_target.len != TARGET_LENGTH || network_target.len != TARGET_LENGTH ||
        start_nonce >= stop_nonce || stop_nonce > NONCE_LIMIT) {
        PyErr_SetString(PyExc_ValueError, "CUDA search arguments are invalid");
        failed = true;
        goto cleanup;
    }
    if (cudaSetDevice(initialized_device) != cudaSuccess ||
        cudaMalloc((void **)&device_header, HEADER_PREFIX_LENGTH) != cudaSuccess ||
        cudaMalloc((void **)&device_share, TARGET_LENGTH) != cudaSuccess ||
        cudaMalloc((void **)&device_network, TARGET_LENGTH) != cudaSuccess ||
        cudaMalloc((void **)&device_candidate, sizeof(candidate)) != cudaSuccess ||
        cudaMalloc((void **)&device_flags, sizeof(flags)) != cudaSuccess ||
        cudaMemcpy(device_header,
                   header_prefix.buf,
                   HEADER_PREFIX_LENGTH,
                   cudaMemcpyHostToDevice) != cudaSuccess ||
        cudaMemcpy(device_share,
                   share_target.buf,
                   TARGET_LENGTH,
                   cudaMemcpyHostToDevice) != cudaSuccess ||
        cudaMemcpy(device_network,
                   network_target.buf,
                   TARGET_LENGTH,
                   cudaMemcpyHostToDevice) != cudaSuccess ||
        cudaMemcpy(device_candidate,
                   &candidate,
                   sizeof(candidate),
                   cudaMemcpyHostToDevice) != cudaSuccess) {
        set_cuda_error();
        failed = true;
        goto cleanup;
    }

    range_size = stop_nonce - start_nonce;
    required_blocks = (range_size + THREADS_PER_BLOCK - 1ULL) / THREADS_PER_BLOCK;
    block_count = (int)(required_blocks < MAX_BLOCKS ? required_blocks : MAX_BLOCKS);
    search_kernel<<<block_count, THREADS_PER_BLOCK>>>(device_header,
                                                      device_share,
                                                      device_network,
                                                      start_nonce,
                                                      range_size,
                                                      device_candidate);
    if (cudaGetLastError() != cudaSuccess || cudaDeviceSynchronize() != cudaSuccess ||
        cudaMemcpy(&candidate,
                   device_candidate,
                   sizeof(candidate),
                   cudaMemcpyDeviceToHost) != cudaSuccess) {
        set_cuda_error();
        failed = true;
        goto cleanup;
    }

    if (candidate != ULLONG_MAX) {
        verify_candidate_kernel<<<1, 1>>>(device_header,
                                          device_share,
                                          device_network,
                                          (uint32_t)candidate,
                                          device_flags);
        if (cudaGetLastError() != cudaSuccess || cudaDeviceSynchronize() != cudaSuccess ||
            cudaMemcpy(flags, device_flags, sizeof(flags), cudaMemcpyDeviceToHost) !=
                cudaSuccess) {
            set_cuda_error();
            failed = true;
            goto cleanup;
        }
    }

cleanup:
    if (device_flags != NULL) {
        (void)cudaFree(device_flags);
    }
    if (device_candidate != NULL) {
        (void)cudaFree(device_candidate);
    }
    if (device_network != NULL) {
        (void)cudaFree(device_network);
    }
    if (device_share != NULL) {
        (void)cudaFree(device_share);
    }
    if (device_header != NULL) {
        (void)cudaFree(device_header);
    }
    PyBuffer_Release(&network_target);
    PyBuffer_Release(&share_target);
    PyBuffer_Release(&header_prefix);
    if (failed) {
        return NULL;
    }
    return build_search_result(
        candidate, flags[0], flags[1], stop_nonce - start_nonce);
}

static PyObject *cuda_close_device(PyObject *self, PyObject *args) {
    (void)self;
    if (!PyArg_ParseTuple(args, "")) {
        return NULL;
    }
    if (initialized_device >= 0) {
        if (cudaSetDevice(initialized_device) != cudaSuccess ||
            cudaDeviceSynchronize() != cudaSuccess) {
            set_cuda_error();
            initialized_device = -1;
            return NULL;
        }
        initialized_device = -1;
    }
    Py_RETURN_NONE;
}

static PyMethodDef cuda_methods[] = {
    {"initialize_device",
     cuda_initialize_device,
     METH_VARARGS,
     "Initialize one explicitly selected CUDA device."},
    {"search_nonce_range",
     cuda_search_nonce_range,
     METH_VARARGS,
     "Search one exact half-open nonce range."},
    {"close_device", cuda_close_device, METH_VARARGS, "Synchronize backend-owned CUDA work."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef cuda_module = {
    PyModuleDef_HEAD_INIT,
    "_cuda",
    "Optional Hashphere CUDA correctness extension.",
    -1,
    cuda_methods,
};

PyMODINIT_FUNC PyInit__cuda(void) {
    return PyModule_Create(&cuda_module);
}
