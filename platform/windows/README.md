# Windows

## Plain Talk

Windows uses the shared HashOrb Python package. The portable Python backend is the required path; native MSVC and Windows CUDA are not claimed as broadly validated yet.

For the shortest path from checkout to a bounded live mining run, use the [Quick Start Guide](../../docs/QUICKSTART.md#windows).

From a normal non-administrator PowerShell session:

```powershell
& .\scripts\install-windows.ps1 -Action install
```

The installer does not change execution policy or install a separate Windows miner. Detailed validation boundaries are documented in [Installation and Packaging](../../docs/13-installation-and-packaging.md).