// Public macOS network topology snapshot; owned and debounced by Rust.
#include <stdint.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <SystemConfiguration/SystemConfiguration.h>

// No counters, transient flags, DNS notifications or subprocesses in this
// fingerprint. Include tunnel membership even before it has an IP address.
// Caller owns the returned buffer. Failure must not look like interface removal.
char *bandpeek_network_topology(void) {
    struct ifaddrs *list = NULL;
    if (getifaddrs(&list) != 0) return NULL;
    char *result = calloc(65536, 1);
    if (!result) { freeifaddrs(list); return NULL; }
    size_t used = 0;
    for (struct ifaddrs *p = list; p; p = p->ifa_next) {
        if (!p->ifa_addr) continue;
        int family = p->ifa_addr->sa_family;
        if (family != AF_LINK && family != AF_INET && family != AF_INET6) continue;
        char address[INET6_ADDRSTRLEN] = "", mask[INET6_ADDRSTRLEN] = "";
        if (family == AF_INET || family == AF_INET6) {
            const void *a = family == AF_INET
                ? (const void *)&((struct sockaddr_in *)p->ifa_addr)->sin_addr
                : (const void *)&((struct sockaddr_in6 *)p->ifa_addr)->sin6_addr;
            inet_ntop(family, a, address, sizeof(address));
            if (p->ifa_netmask) {
                const void *m = family == AF_INET
                    ? (const void *)&((struct sockaddr_in *)p->ifa_netmask)->sin_addr
                    : (const void *)&((struct sockaddr_in6 *)p->ifa_netmask)->sin6_addr;
                inet_ntop(family, m, mask, sizeof(mask));
            }
        }
        int n = snprintf(result + used, 65536 - used, "%s|%u|%u|%d|%s|%s\n",
            p->ifa_name, if_nametoindex(p->ifa_name),
            p->ifa_flags & (IFF_UP | IFF_RUNNING | IFF_POINTOPOINT | IFF_LOOPBACK),
            family, address, mask);
        if (n < 0 || (size_t)n >= 65536 - used) goto fail;
        used += n;
    }
    freeifaddrs(list); list = NULL;
    SCDynamicStoreRef store = SCDynamicStoreCreate(NULL, CFSTR("BandPeek topology"), NULL, NULL);
    if (!store) goto fail;
    const CFStringRef entities[] = { kSCEntNetIPv4, kSCEntNetIPv6 };
    const CFStringRef fields[] = { kSCDynamicStorePropNetPrimaryInterface,
        kSCDynamicStorePropNetPrimaryService, kSCPropNetIPv4Router };
    for (int e = 0; e < 2; e++) {
        CFStringRef key = SCDynamicStoreKeyCreateNetworkGlobalEntity(NULL,
            kSCDynamicStoreDomainState, entities[e]);
        CFPropertyListRef value = SCDynamicStoreCopyValue(store, key);
        CFRelease(key);
        for (int f = 0; f < 3; f++) {
            char text[1024] = "";
            if (value && CFGetTypeID(value) == CFDictionaryGetTypeID()) {
                CFTypeRef field = CFDictionaryGetValue(value, fields[f]);
                if (field && CFGetTypeID(field) == CFStringGetTypeID())
                    CFStringGetCString(field, text, sizeof(text), kCFStringEncodingUTF8);
            }
            int n = snprintf(result + used, 65536 - used, "path%d.%d|%s\n", e, f, text);
            if (n < 0 || (size_t)n >= 65536 - used) {
                if (value) CFRelease(value);
                CFRelease(store); goto fail;
            }
            used += n;
        }
        if (value) CFRelease(value);
    }
    CFRelease(store);
    return result;
fail:
    if (list) freeifaddrs(list);
    free(result);
    return NULL;
}
