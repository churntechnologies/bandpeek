// Thin ABI adapter for public macOS SDK structs; all collection/accounting is Rust.
#include <libproc.h>
#include <sys/types.h>
#include <sys/sysctl.h>
#include <sys/proc.h>
#include <stdint.h>

uint64_t bandpeek_process_birth(int pid) {
    struct proc_bsdinfo info = {0};
    if (proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, sizeof(info)) == sizeof(info)
        && info.pbi_pid == (uint32_t)pid && info.pbi_start_tvsec > 0) {
        return info.pbi_start_tvsec * 1000000ULL + info.pbi_start_tvusec;
    }
    // libproc denies access to some other-user/system processes. KERN_PROC_PID
    // returns their start times to an ordinary user on the validated macOS build.
    struct kinfo_proc process = {0};
    size_t size = sizeof(process);
    int mib[] = {CTL_KERN, KERN_PROC, KERN_PROC_PID, pid};
    if (sysctl(mib, 4, &process, &size, NULL, 0) == 0 && size == sizeof(process)
        && process.kp_proc.p_pid == pid && process.kp_proc.p_starttime.tv_sec > 0) {
        return (uint64_t)process.kp_proc.p_starttime.tv_sec * 1000000ULL
            + process.kp_proc.p_starttime.tv_usec;
    }
    return 0; // Unavailable is explicit: never fall back to PID-only history.
}

#include <mach/mach_time.h>
uint64_t bandpeek_continuous_ms(void) {
    mach_timebase_info_data_t info;
    mach_timebase_info(&info);
    return (uint64_t)((__uint128_t)mach_continuous_time() * info.numer
        / info.denom / 1000000);
}
