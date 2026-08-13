#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <cuda_runtime.h>
#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define HEADER_PREFIX_LENGTH 76
#define TARGET_LENGTH 32
#define DEFAULT_THREADS_PER_BLOCK 256U
#define MAX_BLOCKS 65535
#define NONCE_LIMIT 0x100000000ULL
#define BEST_REDUCTION_THREADS 256U

#define SHA256_CONSTANT_VALUES                                                \
    0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU,         \
    0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U, 0xd807aa98U, 0x12835b01U,         \
    0x243185beU, 0x550c7dc3U, 0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U,         \
    0xc19bf174U, 0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,         \
    0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU, 0x983e5152U,         \
    0xa831c66dU, 0xb00327c8U, 0xbf597fc7U, 0xc6e00bf3U, 0xd5a79147U,         \
    0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU,         \
    0x53380d13U, 0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,         \
    0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U, 0xd192e819U,         \
    0xd6990624U, 0xf40e3585U, 0x106aa070U, 0x19a4c116U, 0x1e376c08U,         \
    0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU,         \
    0x682e6ff3U, 0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,         \
    0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U

static const uint32_t SHA256_CONSTANTS_HOST[64] = {SHA256_CONSTANT_VALUES};
__device__ __constant__ uint32_t SHA256_CONSTANTS[64] = {SHA256_CONSTANT_VALUES};

typedef struct {
    uint32_t midstate[8];
    uint32_t tail_words[3];
    uint32_t share_target[8];
    uint32_t network_target[8];
} PreparedCudaWork;

typedef struct {
    PreparedCudaWork *work;
    unsigned long long *candidate;
    unsigned long long *best_nonce;
    unsigned long long *best_key;
    unsigned long long *block_best_words;
    unsigned long long *block_best_nonces;
    uint8_t *flags;
    uint8_t cached_header[HEADER_PREFIX_LENGTH];
    uint8_t cached_share[TARGET_LENGTH];
    uint8_t cached_network[TARGET_LENGTH];
    bool work_loaded;
} CudaResources;

typedef struct {
    int device_ordinal;
    CudaResources resources;
    bool initialized;
} CudaContext;

#define CUDA_CONTEXT_CAPSULE "hashorb.cuda_context"

static CudaContext legacy_context = {-1, {0}, false};

__host__ __device__ static inline uint32_t rotate_right(uint32_t value,
                                                        uint32_t count) {
    return (value >> count) | (value << (32U - count));
}

static void sha256_transform_host(uint32_t state[8], const uint8_t block[64]) {
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
            h + sum_1 + choice + SHA256_CONSTANTS_HOST[index] + words[index];
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

__device__ __forceinline__ static void sha256_transform_words(
    uint32_t state[8],
    uint32_t word_0,
    uint32_t word_1,
    uint32_t word_2,
    uint32_t word_3,
    uint32_t word_4,
    uint32_t word_5,
    uint32_t word_6,
    uint32_t word_7,
    uint32_t word_8,
    uint32_t word_9,
    uint32_t word_10,
    uint32_t word_11,
    uint32_t word_12,
    uint32_t word_13,
    uint32_t word_14,
    uint32_t word_15) {
    uint32_t words[16] = {
        word_0,  word_1,  word_2,  word_3,  word_4,  word_5,
        word_6,  word_7,  word_8,  word_9,  word_10, word_11,
        word_12, word_13, word_14, word_15,
    };
    uint32_t a = state[0];
    uint32_t b = state[1];
    uint32_t c = state[2];
    uint32_t d = state[3];
    uint32_t e = state[4];
    uint32_t f = state[5];
    uint32_t g = state[6];
    uint32_t h = state[7];

#pragma unroll
    for (int index = 0; index < 64; ++index) {
        int slot = index & 15;
        if (index >= 16) {
            uint32_t previous_15 = words[(index - 15) & 15];
            uint32_t previous_2 = words[(index - 2) & 15];
            uint32_t sigma_0 = rotate_right(previous_15, 7U) ^
                               rotate_right(previous_15, 18U) ^
                               (previous_15 >> 3U);
            uint32_t sigma_1 = rotate_right(previous_2, 17U) ^
                               rotate_right(previous_2, 19U) ^
                               (previous_2 >> 10U);
            words[slot] += sigma_0 + words[(index - 7) & 15] + sigma_1;
        }
        uint32_t sum_1 =
            rotate_right(e, 6U) ^ rotate_right(e, 11U) ^ rotate_right(e, 25U);
        uint32_t choice = (e & f) ^ ((~e) & g);
        uint32_t temporary_1 =
            h + sum_1 + choice + SHA256_CONSTANTS[index] + words[slot];
        uint32_t sum_0 =
            rotate_right(a, 2U) ^ rotate_right(a, 13U) ^ rotate_right(a, 22U);
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

__device__ __forceinline__ static uint32_t reverse_bytes(uint32_t value) {
    return ((value & 0x000000ffU) << 24U) | ((value & 0x0000ff00U) << 8U) |
           ((value & 0x00ff0000U) >> 8U) | ((value & 0xff000000U) >> 24U);
}

__device__ __forceinline__ static void double_sha256_nonce(
    const PreparedCudaWork *work,
    uint32_t nonce,
    uint32_t digest[8]) {
    uint32_t first[8];
#pragma unroll
    for (int index = 0; index < 8; ++index) {
        first[index] = work->midstate[index];
    }
    sha256_transform_words(first,
                           work->tail_words[0],
                           work->tail_words[1],
                           work->tail_words[2],
                           reverse_bytes(nonce),
                           0x80000000U,
                           0U,
                           0U,
                           0U,
                           0U,
                           0U,
                           0U,
                           0U,
                           0U,
                           0U,
                           0U,
                           640U);

    digest[0] = 0x6a09e667U;
    digest[1] = 0xbb67ae85U;
    digest[2] = 0x3c6ef372U;
    digest[3] = 0xa54ff53aU;
    digest[4] = 0x510e527fU;
    digest[5] = 0x9b05688cU;
    digest[6] = 0x1f83d9abU;
    digest[7] = 0x5be0cd19U;
    sha256_transform_words(digest,
                           first[0],
                           first[1],
                           first[2],
                           first[3],
                           first[4],
                           first[5],
                           first[6],
                           first[7],
                           0x80000000U,
                           0U,
                           0U,
                           0U,
                           0U,
                           0U,
                           0U,
                           256U);
}

__device__ __forceinline__ static bool digest_meets_target(
    const uint32_t digest[8],
    const uint32_t target[8]) {
    for (int index = 7; index >= 0; --index) {
        uint32_t digest_word = reverse_bytes(digest[index]);
        if (digest_word < target[index]) {
            return true;
        }
        if (digest_word > target[index]) {
            return false;
        }
    }
    return true;
}

__device__ __forceinline__ static int digest_compare(
    const uint32_t left[8],
    const uint32_t right[8]) {
#pragma unroll
    for (int index = 7; index >= 0; --index) {
        uint32_t left_word = reverse_bytes(left[index]);
        uint32_t right_word = reverse_bytes(right[index]);
        if (left_word < right_word) {
            return -1;
        }
        if (left_word > right_word) {
            return 1;
        }
    }
    return 0;
}

__device__ __forceinline__ static uint32_t digest_high_word(
    const uint32_t digest[8]) {
    return reverse_bytes(digest[7]);
}

__device__ __forceinline__ static unsigned long long digest_high_key(
    const uint32_t digest[8]) {
    return ((unsigned long long)reverse_bytes(digest[7]) << 32U) |
           (unsigned long long)reverse_bytes(digest[6]);
}

__device__ __forceinline__ static bool digest_meets_target_with_high_word(
    const uint32_t digest[8],
    const uint32_t target[8],
    uint32_t high_word) {
    if (high_word < target[7]) {
        return true;
    }
    if (high_word > target[7]) {
        return false;
    }

#pragma unroll
    for (int index = 6; index >= 0; --index) {
        uint32_t digest_word = reverse_bytes(digest[index]);
        if (digest_word < target[index]) {
            return true;
        }
        if (digest_word > target[index]) {
            return false;
        }
    }
    return true;
}

/*
 * Full 256-bit comparison is required only when two hashes have the same
 * most-significant 32 bits. Keeping this out of line avoids carrying another
 * eight-word digest in every hot search lane.
 */
#define BEST_TIE_FLAG (1ULL << 63)
#define BEST_NONCE_MASK 0xffffffffULL

__device__ __forceinline__ static unsigned long long best_nonce_value(
    unsigned long long state) {
    return state & BEST_NONCE_MASK;
}

__device__ __forceinline__ static bool best_state_has_tie(
    unsigned long long state) {
    return state != ULLONG_MAX &&
           (state & BEST_TIE_FLAG) != 0ULL;
}

__device__ __forceinline__ static void merge_compact_best(
    unsigned long long candidate_key,
    unsigned long long candidate_state,
    unsigned long long *current_key,
    unsigned long long *current_state) {
    if (candidate_state == ULLONG_MAX) {
        return;
    }

    if (*current_state == ULLONG_MAX ||
        candidate_key < *current_key) {
        *current_key = candidate_key;
        *current_state = candidate_state;
        return;
    }

    if (candidate_key > *current_key) {
        return;
    }

    unsigned long long candidate_nonce =
        best_nonce_value(candidate_state);
    unsigned long long current_nonce =
        best_nonce_value(*current_state);

    bool tied =
        best_state_has_tie(candidate_state) ||
        best_state_has_tie(*current_state) ||
        candidate_nonce != current_nonce;

    unsigned long long selected_nonce =
        candidate_nonce < current_nonce
            ? candidate_nonce
            : current_nonce;

    *current_state =
        selected_nonce |
        (tied ? BEST_TIE_FLAG : 0ULL);
}


__device__ __forceinline__ static void evaluate_nonce(
    const PreparedCudaWork *work,
    uint32_t nonce,
    bool *meets_share,
    bool *meets_network) {
    uint32_t digest[8];
    double_sha256_nonce(work, nonce, digest);
    *meets_share = digest_meets_target(digest, work->share_target);
    *meets_network = digest_meets_target(digest, work->network_target);
}

__global__ __launch_bounds__(256, 5) static void search_kernel(
    const PreparedCudaWork *work,
    uint64_t start_nonce,
    uint64_t range_size,
    unsigned long long *candidate_nonce,
    unsigned long long *block_best_words,
    unsigned long long *block_best_nonces) {
    extern __shared__ unsigned char shared_storage[];

    unsigned long long *shared_words =
        reinterpret_cast<unsigned long long *>(shared_storage);

    unsigned long long *shared_nonces =
        reinterpret_cast<unsigned long long *>(
            shared_words + blockDim.x);

    uint64_t lane =
        (uint64_t)blockIdx.x * blockDim.x + threadIdx.x;
    uint64_t stride =
        (uint64_t)gridDim.x * blockDim.x;

    unsigned long long local_best_word = ULLONG_MAX;
    unsigned long long local_best_nonce = ULLONG_MAX;

    for (uint64_t offset = lane; offset < range_size; offset += stride) {
        uint32_t nonce = (uint32_t)(start_nonce + offset);
        uint32_t digest[8];

        double_sha256_nonce(work, nonce, digest);

        uint32_t high_word = digest_high_word(digest);
        unsigned long long high_key = digest_high_key(digest);

        merge_compact_best(
            high_key,
            (unsigned long long)nonce,
            &local_best_word,
            &local_best_nonce);

        bool meets_share =
            digest_meets_target_with_high_word(
                digest,
                work->share_target,
                high_word);

        bool meets_network =
            digest_meets_target_with_high_word(
                digest,
                work->network_target,
                high_word);

        if (meets_share || meets_network) {
            atomicMin(
                candidate_nonce,
                (unsigned long long)nonce);
        }
    }

    shared_words[threadIdx.x] = local_best_word;
    shared_nonces[threadIdx.x] = local_best_nonce;
    __syncthreads();

    for (unsigned int width = blockDim.x >> 1U;
         width > 0;
         width >>= 1U) {
        if (threadIdx.x < width) {
            unsigned int peer = threadIdx.x + width;

            merge_compact_best(
                shared_words[peer],
                shared_nonces[peer],
                &shared_words[threadIdx.x],
                &shared_nonces[threadIdx.x]);
        }
        __syncthreads();
    }

    if (threadIdx.x == 0) {
        block_best_words[blockIdx.x] =
            shared_words[0];
        block_best_nonces[blockIdx.x] =
            shared_nonces[0];
    }
}

__global__ __launch_bounds__(512, 1) static void search_kernel_512(
    const PreparedCudaWork *work,
    uint64_t start_nonce,
    uint64_t range_size,
    unsigned long long *candidate_nonce,
    unsigned long long *block_best_words,
    unsigned long long *block_best_nonces) {
    extern __shared__ unsigned char shared_storage[];

    unsigned long long *shared_words =
        reinterpret_cast<unsigned long long *>(shared_storage);

    unsigned long long *shared_nonces =
        reinterpret_cast<unsigned long long *>(
            shared_words + blockDim.x);

    uint64_t lane =
        (uint64_t)blockIdx.x * blockDim.x + threadIdx.x;
    uint64_t stride =
        (uint64_t)gridDim.x * blockDim.x;

    unsigned long long local_best_word = ULLONG_MAX;
    unsigned long long local_best_nonce = ULLONG_MAX;

    for (uint64_t offset = lane; offset < range_size; offset += stride) {
        uint32_t nonce = (uint32_t)(start_nonce + offset);
        uint32_t digest[8];

        double_sha256_nonce(work, nonce, digest);

        uint32_t high_word = digest_high_word(digest);
        unsigned long long high_key = digest_high_key(digest);

        merge_compact_best(
            high_key,
            (unsigned long long)nonce,
            &local_best_word,
            &local_best_nonce);

        bool meets_share =
            digest_meets_target_with_high_word(
                digest,
                work->share_target,
                high_word);

        bool meets_network =
            digest_meets_target_with_high_word(
                digest,
                work->network_target,
                high_word);

        if (meets_share || meets_network) {
            atomicMin(
                candidate_nonce,
                (unsigned long long)nonce);
        }
    }

    shared_words[threadIdx.x] = local_best_word;
    shared_nonces[threadIdx.x] = local_best_nonce;
    __syncthreads();

    for (unsigned int width = blockDim.x >> 1U;
         width > 0;
         width >>= 1U) {
        if (threadIdx.x < width) {
            unsigned int peer = threadIdx.x + width;

            merge_compact_best(
                shared_words[peer],
                shared_nonces[peer],
                &shared_words[threadIdx.x],
                &shared_nonces[threadIdx.x]);
        }
        __syncthreads();
    }

    if (threadIdx.x == 0) {
        block_best_words[blockIdx.x] =
            shared_words[0];
        block_best_nonces[blockIdx.x] =
            shared_nonces[0];
    }
}

__global__ static void reduce_block_bests_kernel(
    const PreparedCudaWork *work,
    const unsigned long long *block_best_words,
    const unsigned long long *block_best_nonces,
    int block_count,
    unsigned long long *best_key,
    unsigned long long *best_nonce) {
    extern __shared__ unsigned char shared_storage[];

    unsigned long long *shared_words =
        reinterpret_cast<unsigned long long *>(shared_storage);

    unsigned long long *shared_nonces =
        reinterpret_cast<unsigned long long *>(
            shared_words + blockDim.x);

    unsigned long long local_best_word = ULLONG_MAX;
    unsigned long long local_best_nonce = ULLONG_MAX;

    for (int index = (int)threadIdx.x;
         index < block_count;
         index += (int)blockDim.x) {
        merge_compact_best(
            block_best_words[index],
            block_best_nonces[index],
            &local_best_word,
            &local_best_nonce);
    }

    shared_words[threadIdx.x] = local_best_word;
    shared_nonces[threadIdx.x] = local_best_nonce;
    __syncthreads();

    for (unsigned int width = blockDim.x >> 1U;
         width > 0;
         width >>= 1U) {
        if (threadIdx.x < width) {
            unsigned int peer = threadIdx.x + width;

            merge_compact_best(
                shared_words[peer],
                shared_nonces[peer],
                &shared_words[threadIdx.x],
                &shared_nonces[threadIdx.x]);
        }
        __syncthreads();
    }

    if (threadIdx.x == 0) {
        *best_key = shared_words[0];
        *best_nonce = shared_nonces[0];
    }
}

__device__ __noinline__ static bool exact_nonce_is_better(
    const PreparedCudaWork *work,
    unsigned long long candidate_nonce,
    unsigned long long current_nonce) {
    if (candidate_nonce == ULLONG_MAX) {
        return false;
    }
    if (current_nonce == ULLONG_MAX) {
        return true;
    }

    uint32_t candidate_digest[8];
    uint32_t current_digest[8];

    double_sha256_nonce(
        work,
        (uint32_t)candidate_nonce,
        candidate_digest);

    double_sha256_nonce(
        work,
        (uint32_t)current_nonce,
        current_digest);

    int comparison =
        digest_compare(candidate_digest, current_digest);

    return comparison < 0 ||
           (comparison == 0 &&
            candidate_nonce < current_nonce);
}


__global__ static void resolve_tied_best_kernel(
    const PreparedCudaWork *work,
    uint64_t start_nonce,
    uint64_t range_size,
    unsigned long long best_key,
    unsigned long long key_mask,
    unsigned long long *block_best_nonces) {
    extern __shared__ unsigned long long shared_nonces[];

    uint64_t lane =
        (uint64_t)blockIdx.x * blockDim.x + threadIdx.x;

    uint64_t stride =
        (uint64_t)gridDim.x * blockDim.x;

    unsigned long long local_best_nonce = ULLONG_MAX;

    for (uint64_t offset = lane;
         offset < range_size;
         offset += stride) {
        uint32_t nonce =
            (uint32_t)(start_nonce + offset);

        uint32_t digest[8];
        double_sha256_nonce(work, nonce, digest);

        unsigned long long high_key =
            digest_high_key(digest);

        if ((high_key & key_mask) !=
            (best_key & key_mask)) {
            continue;
        }

        unsigned long long candidate_nonce =
            (unsigned long long)nonce;

        if (exact_nonce_is_better(
                work,
                candidate_nonce,
                local_best_nonce)) {
            local_best_nonce = candidate_nonce;
        }
    }

    shared_nonces[threadIdx.x] = local_best_nonce;
    __syncthreads();

    for (unsigned int width = blockDim.x >> 1U;
         width > 0;
         width >>= 1U) {
        if (threadIdx.x < width) {
            unsigned int peer = threadIdx.x + width;

            if (exact_nonce_is_better(
                    work,
                    shared_nonces[peer],
                    shared_nonces[threadIdx.x])) {
                shared_nonces[threadIdx.x] =
                    shared_nonces[peer];
            }
        }

        __syncthreads();
    }

    if (threadIdx.x == 0) {
        block_best_nonces[blockIdx.x] =
            shared_nonces[0];
    }
}


__global__ static void reduce_resolved_bests_kernel(
    const PreparedCudaWork *work,
    const unsigned long long *block_best_nonces,
    int block_count,
    unsigned long long *best_nonce) {
    extern __shared__ unsigned long long shared_nonces[];

    unsigned long long local_best_nonce = ULLONG_MAX;

    for (int index = (int)threadIdx.x;
         index < block_count;
         index += (int)blockDim.x) {
        if (exact_nonce_is_better(
                work,
                block_best_nonces[index],
                local_best_nonce)) {
            local_best_nonce =
                block_best_nonces[index];
        }
    }

    shared_nonces[threadIdx.x] = local_best_nonce;
    __syncthreads();

    for (unsigned int width = blockDim.x >> 1U;
         width > 0;
         width >>= 1U) {
        if (threadIdx.x < width) {
            unsigned int peer = threadIdx.x + width;

            if (exact_nonce_is_better(
                    work,
                    shared_nonces[peer],
                    shared_nonces[threadIdx.x])) {
                shared_nonces[threadIdx.x] =
                    shared_nonces[peer];
            }
        }

        __syncthreads();
    }

    if (threadIdx.x == 0) {
        *best_nonce = shared_nonces[0];
    }
}


__global__ static void verify_candidate_kernel(const PreparedCudaWork *work,
                                               uint32_t nonce,
                                               uint8_t *flags) {
    bool meets_share = false;
    bool meets_network = false;
    evaluate_nonce(work, nonce, &meets_share, &meets_network);
    flags[0] = meets_share ? 1U : 0U;
    flags[1] = meets_network ? 1U : 0U;
}

static uint32_t load_big_endian_word(const uint8_t *bytes) {
    return ((uint32_t)bytes[0] << 24U) | ((uint32_t)bytes[1] << 16U) |
           ((uint32_t)bytes[2] << 8U) | (uint32_t)bytes[3];
}

static uint32_t load_little_endian_word(const uint8_t *bytes) {
    return (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8U) |
           ((uint32_t)bytes[2] << 16U) | ((uint32_t)bytes[3] << 24U);
}

static void prepare_cuda_work(PreparedCudaWork *prepared,
                              const uint8_t *header_prefix,
                              const uint8_t *share_target,
                              const uint8_t *network_target) {
    uint32_t state[8] = {
        0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
        0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U,
    };
    sha256_transform_host(state, header_prefix);
    memcpy(prepared->midstate, state, sizeof(state));
    for (int index = 0; index < 3; ++index) {
        prepared->tail_words[index] =
            load_big_endian_word(header_prefix + 64 + index * 4);
    }
    for (int index = 0; index < 8; ++index) {
        prepared->share_target[index] =
            load_little_endian_word(share_target + index * 4);
        prepared->network_target[index] =
            load_little_endian_word(network_target + index * 4);
    }
}

static void release_resources(CudaContext *context) {
    CudaResources *owned = &context->resources;
    if (owned->flags != NULL) {
        (void)cudaFree(owned->flags);
    }
    if (owned->block_best_nonces != NULL) {
        (void)cudaFree(owned->block_best_nonces);
    }
    if (owned->block_best_words != NULL) {
        (void)cudaFree(owned->block_best_words);
    }
    if (owned->best_key != NULL) {
        (void)cudaFree(owned->best_key);
    }
    if (owned->best_nonce != NULL) {
        (void)cudaFree(owned->best_nonce);
    }
    if (owned->candidate != NULL) {
        (void)cudaFree(owned->candidate);
    }
    if (owned->work != NULL) {
        (void)cudaFree(owned->work);
    }
    memset(owned, 0, sizeof(*owned));
}


static bool upload_work_if_changed(CudaContext *context,
                                   const uint8_t *header_prefix,
                                   const uint8_t *share_target,
                                   const uint8_t *network_target) {
    CudaResources *owned = &context->resources;
    if (owned->work_loaded &&
        memcmp(owned->cached_header, header_prefix, HEADER_PREFIX_LENGTH) == 0 &&
        memcmp(owned->cached_share, share_target, TARGET_LENGTH) == 0 &&
        memcmp(owned->cached_network, network_target, TARGET_LENGTH) == 0) {
        return true;
    }
    PreparedCudaWork prepared;
    prepare_cuda_work(&prepared, header_prefix, share_target, network_target);
    if (cudaMemcpy(owned->work,
                   &prepared,
                   sizeof(prepared),
                   cudaMemcpyHostToDevice) != cudaSuccess) {
        return false;
    }
    memcpy(owned->cached_header, header_prefix, HEADER_PREFIX_LENGTH);
    memcpy(owned->cached_share, share_target, TARGET_LENGTH);
    memcpy(owned->cached_network, network_target, TARGET_LENGTH);
    owned->work_loaded = true;
    return true;
}

static void set_cuda_error(void) {
    PyErr_SetString(PyExc_RuntimeError, "CUDA operation failed");
}

static bool close_context(CudaContext *context) {
    if (!context->initialized) {
        return true;
    }
    bool failed = cudaSetDevice(context->device_ordinal) != cudaSuccess ||
                  cudaDeviceSynchronize() != cudaSuccess;
    release_resources(context);
    context->device_ordinal = -1;
    context->initialized = false;
    return !failed;
}

static bool initialize_context(CudaContext *context, int device_ordinal) {
    int device_count;
    if (context->initialized && !close_context(context)) {
        return false;
    }
    if (cudaGetDeviceCount(&device_count) != cudaSuccess ||
        device_ordinal >= device_count || cudaSetDevice(device_ordinal) != cudaSuccess ||
        cudaFree(NULL) != cudaSuccess ||
        cudaMalloc((void **)&context->resources.work,
                   sizeof(PreparedCudaWork)) != cudaSuccess ||
        cudaMalloc((void **)&context->resources.candidate,
                   sizeof(unsigned long long)) != cudaSuccess ||
        cudaMalloc((void **)&context->resources.best_nonce,
                   sizeof(unsigned long long)) != cudaSuccess ||
        cudaMalloc((void **)&context->resources.best_key,
                   sizeof(unsigned long long)) != cudaSuccess ||
        cudaMalloc((void **)&context->resources.block_best_words,
                   (size_t)MAX_BLOCKS * sizeof(unsigned long long)) != cudaSuccess ||
        cudaMalloc((void **)&context->resources.block_best_nonces,
                   (size_t)MAX_BLOCKS * sizeof(unsigned long long)) != cudaSuccess ||
        cudaMalloc((void **)&context->resources.flags, 2U) != cudaSuccess) {
        release_resources(context);
        context->device_ordinal = -1;
        context->initialized = false;
        return false;
    }
    context->device_ordinal = device_ordinal;
    context->initialized = true;
    return true;
}


static PyObject *cuda_initialize_device(PyObject *self, PyObject *args) {
    int device_ordinal;
    bool succeeded;
    (void)self;
    if (!PyArg_ParseTuple(args, "i", &device_ordinal)) {
        return NULL;
    }
    if (device_ordinal < 0) {
        PyErr_SetString(PyExc_ValueError, "CUDA device ordinal is invalid");
        return NULL;
    }
    Py_BEGIN_ALLOW_THREADS
    succeeded = initialize_context(&legacy_context, device_ordinal);
    Py_END_ALLOW_THREADS
    if (!succeeded) {
        set_cuda_error();
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *build_search_result(unsigned long long candidate,
                                     unsigned char meets_share,
                                     unsigned char meets_network,
                                     unsigned long long hashes_checked,
                                     unsigned long long best_nonce) {
    PyObject *result = PyTuple_New(5);
    if (result == NULL) {
        return NULL;
    }
    PyObject *nonce_object =
        candidate == ULLONG_MAX ? Py_NewRef(Py_None) : PyLong_FromUnsignedLongLong(candidate);
    PyObject *share_object = PyBool_FromLong(meets_share != 0U);
    PyObject *network_object = PyBool_FromLong(meets_network != 0U);
    PyObject *count_object = PyLong_FromUnsignedLongLong(hashes_checked);
    PyObject *best_nonce_object =
        best_nonce == ULLONG_MAX ? Py_NewRef(Py_None) : PyLong_FromUnsignedLongLong(best_nonce);

    if (nonce_object == NULL || share_object == NULL || network_object == NULL ||
        count_object == NULL || best_nonce_object == NULL) {
        Py_XDECREF(nonce_object);
        Py_XDECREF(share_object);
        Py_XDECREF(network_object);
        Py_XDECREF(count_object);
        Py_XDECREF(best_nonce_object);
        Py_DECREF(result);
        return NULL;
    }

    PyTuple_SET_ITEM(result, 0, nonce_object);
    PyTuple_SET_ITEM(result, 1, share_object);
    PyTuple_SET_ITEM(result, 2, network_object);
    PyTuple_SET_ITEM(result, 3, count_object);
    PyTuple_SET_ITEM(result, 4, best_nonce_object);
    return result;
}


static bool search_context(CudaContext *context,
                           const uint8_t *header_prefix,
                           const uint8_t *share_target,
                           const uint8_t *network_target,
                           unsigned long long start_nonce,
                           unsigned long long stop_nonce,
                           unsigned int threads_per_block,
                           unsigned long long *candidate,
                           unsigned long long *best_nonce,
                           unsigned char flags[2]) {
    unsigned long long best_key = ULLONG_MAX;

    if (!context->initialized || cudaSetDevice(context->device_ordinal) != cudaSuccess ||
        !upload_work_if_changed(context,
                                header_prefix,
                                share_target,
                                network_target) ||
        cudaMemset(context->resources.candidate, 0xff, sizeof(*candidate)) != cudaSuccess ||
        cudaMemset(context->resources.best_nonce, 0xff, sizeof(*best_nonce)) != cudaSuccess) {
        return false;
    }

    unsigned long long range_size = stop_nonce - start_nonce;
    unsigned long long required_blocks =
        (range_size + threads_per_block - 1ULL) / threads_per_block;
    int block_count =
        (int)(required_blocks < MAX_BLOCKS ? required_blocks : MAX_BLOCKS);

    size_t search_shared_bytes =
        (size_t)threads_per_block *
        (2U * sizeof(unsigned long long));

    if (threads_per_block == 512U) {
        search_kernel_512<<<
            block_count,
            threads_per_block,
            search_shared_bytes>>>(
                context->resources.work,
                start_nonce,
                range_size,
                context->resources.candidate,
                context->resources.block_best_words,
                context->resources.block_best_nonces);
    } else {
        search_kernel<<<
            block_count,
            threads_per_block,
            search_shared_bytes>>>(
                context->resources.work,
                start_nonce,
                range_size,
                context->resources.candidate,
                context->resources.block_best_words,
                context->resources.block_best_nonces);
    }

    if (cudaGetLastError() != cudaSuccess) {
        return false;
    }

    size_t reduction_shared_bytes =
        (size_t)BEST_REDUCTION_THREADS *
        (2U * sizeof(unsigned long long));

    reduce_block_bests_kernel<<<1, BEST_REDUCTION_THREADS, reduction_shared_bytes>>>(
        context->resources.work,
        context->resources.block_best_words,
        context->resources.block_best_nonces,
        block_count,
        context->resources.best_key,
        context->resources.best_nonce);

    if (cudaGetLastError() != cudaSuccess ||
        cudaMemcpy(candidate,
                   context->resources.candidate,
                   sizeof(*candidate),
                   cudaMemcpyDeviceToHost) != cudaSuccess ||
        cudaMemcpy(&best_key,
                   context->resources.best_key,
                   sizeof(best_key),
                   cudaMemcpyDeviceToHost) != cudaSuccess ||
        cudaMemcpy(best_nonce,
                   context->resources.best_nonce,
                   sizeof(*best_nonce),
                   cudaMemcpyDeviceToHost) != cudaSuccess) {
        return false;
    }

    if (*best_nonce == ULLONG_MAX ||
        best_key == ULLONG_MAX) {
        return false;
    }

    unsigned long long best_state = *best_nonce;

    bool best_tied =
        (best_state & BEST_TIE_FLAG) != 0ULL;

    *best_nonce =
        best_state & BEST_NONCE_MASK;

    const char *force_fallback_value =
        getenv("HASHORB_CUDA_FORCE_BEST_FALLBACK");

    bool force_fallback =
        force_fallback_value != NULL &&
        strcmp(force_fallback_value, "1") == 0;

    if (best_tied || force_fallback) {
        /*
         * Production ties use all 64 prefix bits.
         *
         * The explicit test mode uses a zero mask so every hash
         * participates. This gives hardware tests a deterministic
         * way to execute the rare exact resolver.
         */
        unsigned long long key_mask =
            force_fallback ? 0ULL : ULLONG_MAX;

        /*
         * Erase the compact representative. From this point the
         * returned nonce must come from the exact fallback.
         */
        *best_nonce = ULLONG_MAX;

        if (cudaMemset(
                context->resources.best_nonce,
                0xff,
                sizeof(*best_nonce)) != cudaSuccess) {
            return false;
        }

        size_t fallback_shared_bytes =
            (size_t)BEST_REDUCTION_THREADS *
            sizeof(unsigned long long);

        resolve_tied_best_kernel<<<
            block_count,
            BEST_REDUCTION_THREADS,
            fallback_shared_bytes>>>(
                context->resources.work,
                start_nonce,
                range_size,
                best_key,
                key_mask,
                context->resources.block_best_nonces);

        if (cudaGetLastError() != cudaSuccess) {
            return false;
        }

        reduce_resolved_bests_kernel<<<
            1,
            BEST_REDUCTION_THREADS,
            fallback_shared_bytes>>>(
                context->resources.work,
                context->resources.block_best_nonces,
                block_count,
                context->resources.best_nonce);

        if (cudaGetLastError() != cudaSuccess ||
            cudaMemcpy(
                best_nonce,
                context->resources.best_nonce,
                sizeof(*best_nonce),
                cudaMemcpyDeviceToHost) != cudaSuccess ||
            *best_nonce == ULLONG_MAX) {
            return false;
        }
    }

    if (*candidate != ULLONG_MAX) {
        verify_candidate_kernel<<<1, 1>>>(context->resources.work,
                                          (uint32_t)*candidate,
                                          context->resources.flags);
        if (cudaGetLastError() != cudaSuccess ||
            cudaMemcpy(flags,
                       context->resources.flags,
                       2U,
                       cudaMemcpyDeviceToHost) != cudaSuccess) {
            return false;
        }
    }
    return true;
}


static PyObject *cuda_search_nonce_range(PyObject *self, PyObject *args) {
    Py_buffer header_prefix = {0};
    Py_buffer share_target = {0};
    Py_buffer network_target = {0};
    unsigned long long start_nonce;
    unsigned long long stop_nonce;
    unsigned int threads_per_block = DEFAULT_THREADS_PER_BLOCK;
    unsigned long long candidate = ULLONG_MAX;
    unsigned long long best_nonce = ULLONG_MAX;
    unsigned char flags[2] = {0U, 0U};
    bool succeeded;
    (void)self;

    if (!PyArg_ParseTuple(args,
                          "y*y*y*KK|I",
                          &header_prefix,
                          &share_target,
                          &network_target,
                          &start_nonce,
                          &stop_nonce,
                          &threads_per_block)) {
        return NULL;
    }
    if (!legacy_context.initialized ||
        header_prefix.len != HEADER_PREFIX_LENGTH ||
        share_target.len != TARGET_LENGTH || network_target.len != TARGET_LENGTH ||
        start_nonce >= stop_nonce || stop_nonce > NONCE_LIMIT ||
        (threads_per_block != 64U && threads_per_block != 128U &&
         threads_per_block != 256U && threads_per_block != 512U)) {
        PyErr_SetString(PyExc_ValueError, "CUDA search arguments are invalid");
        goto cleanup;
    }

    Py_BEGIN_ALLOW_THREADS
    succeeded = search_context(&legacy_context,
                               (const uint8_t *)header_prefix.buf,
                               (const uint8_t *)share_target.buf,
                               (const uint8_t *)network_target.buf,
                               start_nonce,
                               stop_nonce,
                               threads_per_block,
                               &candidate,
                               &best_nonce,
                               flags);
    Py_END_ALLOW_THREADS
    if (!succeeded) {
        set_cuda_error();
    }

cleanup:
    PyBuffer_Release(&network_target);
    PyBuffer_Release(&share_target);
    PyBuffer_Release(&header_prefix);
    if (PyErr_Occurred()) {
        return NULL;
    }
    return build_search_result(
        candidate, flags[0], flags[1], stop_nonce - start_nonce, best_nonce);
}


static PyObject *cuda_close_device(PyObject *self, PyObject *args) {
    bool succeeded;
    (void)self;
    if (!PyArg_ParseTuple(args, "")) {
        return NULL;
    }
    Py_BEGIN_ALLOW_THREADS
    succeeded = close_context(&legacy_context);
    Py_END_ALLOW_THREADS
    if (!succeeded) {
        set_cuda_error();
        return NULL;
    }
    Py_RETURN_NONE;
}

static CudaContext *context_from_capsule(PyObject *capsule) {
    return (CudaContext *)PyCapsule_GetPointer(capsule, CUDA_CONTEXT_CAPSULE);
}

static void cuda_context_capsule_destructor(PyObject *capsule) {
    if (!PyCapsule_IsValid(capsule, CUDA_CONTEXT_CAPSULE)) {
        return;
    }
    CudaContext *context = context_from_capsule(capsule);
    if (context == NULL) {
        PyErr_Clear();
        return;
    }
    Py_BEGIN_ALLOW_THREADS
    (void)close_context(context);
    Py_END_ALLOW_THREADS
    PyMem_Free(context);
}

static PyObject *cuda_create_device_context(PyObject *self, PyObject *args) {
    int device_ordinal;
    bool succeeded;
    (void)self;
    if (!PyArg_ParseTuple(args, "i", &device_ordinal)) {
        return NULL;
    }
    if (device_ordinal < 0) {
        PyErr_SetString(PyExc_ValueError, "CUDA device ordinal is invalid");
        return NULL;
    }
    CudaContext *context = (CudaContext *)PyMem_Calloc(1, sizeof(CudaContext));
    if (context == NULL) {
        return PyErr_NoMemory();
    }
    context->device_ordinal = -1;
    Py_BEGIN_ALLOW_THREADS
    succeeded = initialize_context(context, device_ordinal);
    Py_END_ALLOW_THREADS
    if (!succeeded) {
        PyMem_Free(context);
        set_cuda_error();
        return NULL;
    }
    PyObject *capsule =
        PyCapsule_New(context, CUDA_CONTEXT_CAPSULE, cuda_context_capsule_destructor);
    if (capsule == NULL) {
        Py_BEGIN_ALLOW_THREADS
        (void)close_context(context);
        Py_END_ALLOW_THREADS
        PyMem_Free(context);
        return NULL;
    }
    return capsule;
}

static PyObject *cuda_search_device_context(PyObject *self, PyObject *args) {
    PyObject *capsule;
    Py_buffer header_prefix = {0};
    Py_buffer share_target = {0};
    Py_buffer network_target = {0};
    unsigned long long start_nonce;
    unsigned long long stop_nonce;
    unsigned int threads_per_block = DEFAULT_THREADS_PER_BLOCK;
    unsigned long long candidate = ULLONG_MAX;
    unsigned long long best_nonce = ULLONG_MAX;
    unsigned char flags[2] = {0U, 0U};
    bool succeeded;
    (void)self;

    if (!PyArg_ParseTuple(args,
                          "Oy*y*y*KK|I",
                          &capsule,
                          &header_prefix,
                          &share_target,
                          &network_target,
                          &start_nonce,
                          &stop_nonce,
                          &threads_per_block)) {
        return NULL;
    }
    CudaContext *context = context_from_capsule(capsule);
    if (context == NULL || !context->initialized ||
        header_prefix.len != HEADER_PREFIX_LENGTH ||
        share_target.len != TARGET_LENGTH || network_target.len != TARGET_LENGTH ||
        start_nonce >= stop_nonce || stop_nonce > NONCE_LIMIT ||
        (threads_per_block != 64U && threads_per_block != 128U &&
         threads_per_block != 256U && threads_per_block != 512U)) {
        if (context != NULL) {
            PyErr_SetString(PyExc_ValueError, "CUDA context search arguments are invalid");
        }
        goto cleanup;
    }

    Py_BEGIN_ALLOW_THREADS
    succeeded = search_context(context,
                               (const uint8_t *)header_prefix.buf,
                               (const uint8_t *)share_target.buf,
                               (const uint8_t *)network_target.buf,
                               start_nonce,
                               stop_nonce,
                               threads_per_block,
                               &candidate,
                               &best_nonce,
                               flags);
    Py_END_ALLOW_THREADS
    if (!succeeded) {
        set_cuda_error();
    }

cleanup:
    PyBuffer_Release(&network_target);
    PyBuffer_Release(&share_target);
    PyBuffer_Release(&header_prefix);
    if (PyErr_Occurred()) {
        return NULL;
    }
    return build_search_result(
        candidate, flags[0], flags[1], stop_nonce - start_nonce, best_nonce);
}


static PyObject *cuda_close_device_context(PyObject *self, PyObject *args) {
    PyObject *capsule;
    bool succeeded;
    (void)self;
    if (!PyArg_ParseTuple(args, "O", &capsule)) {
        return NULL;
    }
    CudaContext *context = context_from_capsule(capsule);
    if (context == NULL) {
        return NULL;
    }
    Py_BEGIN_ALLOW_THREADS
    succeeded = close_context(context);
    Py_END_ALLOW_THREADS
    if (!succeeded) {
        set_cuda_error();
        return NULL;
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
    {"create_device_context",
     cuda_create_device_context,
     METH_VARARGS,
     "Create one independently owned CUDA device context."},
    {"search_device_context",
     cuda_search_device_context,
     METH_VARARGS,
     "Search one exact range through an owned CUDA device context."},
    {"close_device_context",
     cuda_close_device_context,
     METH_VARARGS,
     "Close one independently owned CUDA device context."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef cuda_module = {
    PyModuleDef_HEAD_INIT,
    "_cuda",
    "Optional HashOrb CUDA correctness extension.",
    -1,
    cuda_methods,
};

PyMODINIT_FUNC PyInit__cuda(void) {
    return PyModule_Create(&cuda_module);
}
