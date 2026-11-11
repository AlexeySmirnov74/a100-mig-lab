import os
import math
import sys
import time
import torch


def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v not in (None, "") else default


def env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    return float(v) if v not in (None, "") else default


def mib(x: int) -> float:
    return x / 1024 / 1024


def is_probable_oom(exc: BaseException) -> bool:
    text = str(exc).lower()
    patterns = [
        "out of memory",
        "cuda out of memory",
        "cuda error: out of memory",
    ]
    return any(p in text for p in patterns)


def print_memory(prefix: str, device: torch.device) -> None:
    allocated = torch.cuda.memory_allocated(device)
    reserved = torch.cuda.memory_reserved(device)
    peak = torch.cuda.max_memory_allocated(device)
    print(
        f"{prefix} allocated={mib(allocated):.1f} MiB "
        f"reserved={mib(reserved):.1f} MiB "
        f"peak={mib(peak):.1f} MiB",
        flush=True,
    )


def main() -> None:
    if not torch.cuda.is_available():
        print("CUDA is not available", flush=True)
        sys.exit(1)

    if torch.cuda.device_count() != 1:
        raise RuntimeError("Expose exactly one dedicated MIG device to the stress worker")

    device = torch.device("cuda:0")
    gpu_name = torch.cuda.get_device_name(device)

    dtype_name = os.getenv("STRESS_DTYPE", "float16").lower()
    if dtype_name == "float32":
        dtype = torch.float32
    elif dtype_name == "bfloat16":
        dtype = torch.bfloat16
    elif dtype_name == "float16":
        dtype = torch.float16
    else:
        raise ValueError("STRESS_DTYPE must be float16, float32, or bfloat16")

    step_mib = env_int("STRESS_STEP_MIB", 128)
    sleep_sec = env_float("STRESS_SLEEP_SEC", 2.0)
    hold_after_oom = env_float("STRESS_HOLD_AFTER_OOM_SEC", 15.0)
    if step_mib <= 0:
        raise ValueError("STRESS_STEP_MIB must be positive")
    if not all(math.isfinite(value) and value >= 0 for value in (sleep_sec, hold_after_oom)):
        raise ValueError("Stress sleep durations must be nonnegative and finite")

    elem_size = torch.tensor([], dtype=dtype).element_size()
    elems_per_step = (step_mib * 1024 * 1024) // elem_size

    print("=" * 80, flush=True)
    print("Starting OOM stress worker", flush=True)
    print(f"GPU: {gpu_name}", flush=True)
    print(f"Device: {device}", flush=True)
    print(f"DType: {dtype}", flush=True)
    print(f"Step size: {step_mib} MiB", flush=True)
    print(f"Sleep between steps: {sleep_sec} sec", flush=True)
    print("=" * 80, flush=True)

    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats(device)

    chunks = []
    step = 0

    try:
        while True:
            step += 1
            print(f"[oom-demo] allocating step={step} size={step_mib} MiB", flush=True)

            t = torch.empty((elems_per_step,), device=device, dtype=dtype)
            t.fill_(1)
            chunks.append(t)

            torch.cuda.synchronize(device)
            print_memory(f"[oom-demo] step={step}", device)

            time.sleep(sleep_sec)

    except torch.OutOfMemoryError as e:
        print("", flush=True)
        print("!" * 80, flush=True)
        print("OOM REACHED ON THIS MIG SLICE", flush=True)
        print(str(e), flush=True)
        print_memory("[oom-demo] final", device)
        print("Other MIG slices should remain healthy if isolation works correctly.", flush=True)
        print("!" * 80, flush=True)
        print("", flush=True)
        time.sleep(hold_after_oom)
        sys.exit(1)

    except RuntimeError as e:
        if is_probable_oom(e):
            print("", flush=True)
            print("!" * 80, flush=True)
            print("OOM / ALLOCATOR FAILURE REACHED ON THIS MIG SLICE", flush=True)
            print(str(e), flush=True)
            try:
                print_memory("[oom-demo] final", device)
            except Exception:
                pass
            print("Other MIG slices should remain healthy if isolation works correctly.", flush=True)
            print("!" * 80, flush=True)
            print("", flush=True)
            time.sleep(hold_after_oom)
            sys.exit(1)
        raise


if __name__ == "__main__":
    main()
