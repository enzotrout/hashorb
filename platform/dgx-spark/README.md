# DGX Spark Validation Boundary

The Spark uses the same shared package. Its CUDA extension is a local explicit
`sm_121` build with host-toolkit RUNPATHs, not a redistributable CUDA wheel.
Hardware evidence belongs in the shared CUDA/profile documentation; no Spark
miner source lives here.
