# Match the confirmed datacenter timezone

Use this during deployment and when server/desktop/Claude time differs from the
selected server region. Reuse the user's known location or explicit IANA choice.
For example, a confirmed Los Angeles datacenter maps to `America/Los_Angeles`,
not the Windows computer's timezone or a fixed `UTC-8` offset. Named zones handle
daylight saving time. Do not make Los Angeles a default for other deployments.

System-image defaults, hostnames and IP geolocation are not proof of physical
location. Prefer the user's chosen IANA zone, then an unambiguous datacenter city
from the existing provider/order information. If location is unknown or conflicts
with an exit-IP result, ask which region to follow. Do not automatically send IPs
to geolocation services. Do not change the local Windows timezone.

## Configure and read back

Run on the server, using the already confirmed zone and intended desktop account:

```text
python3 scripts/timezone_config.py check --desktop-user DESKTOP_USER
python3 scripts/timezone_config.py configure --timezone CONFIRMED_IANA_ZONE --desktop-user DESKTOP_USER
python3 scripts/timezone_config.py configure --timezone CONFIRMED_IANA_ZONE --desktop-user DESKTOP_USER --apply
```

Preview is read-only. Apply uses `timedatectl set-timezone` only if needed and
checks readback. It does not set the clock, toggle NTP, install a time service,
change hardware-clock mode, log out the user or restart RDP/Claude. NTP is reported
separately: timezone changes do not establish that the clock is synchronized.
If synchronization is unavailable, inspect the existing time service and report
its state rather than treating a matching timezone as proof of correct time.

The session report inspects only the chosen user's XFCE/Claude `TZ` environment
overrides and XFCE clock timezone settings. Other environment values are not
printed. A conflicting override needs a scoped repair: inspect the relevant
session/launcher setting or clock plugin, back up before editing, and preserve
unrelated user settings. For XFCE clocks, an empty timezone follows the system;
use the live user's `xfconf-query` when updating an active plugin so cached config
does not overwrite a direct XML edit. Avoid pinning a clock to a stale zone.

## Verify application state honestly

After readback, compare the remote desktop clock and refresh the relevant browser
timezone-check page. A running Electron/Chromium process may cache its old zone.
If it remains stale, save work and reopen Claude normally, then recheck the page.
Do not kill Claude, restart xrdp or disconnect a desktop automatically. Merely
reading `/etc/localtime` or finding no `TZ` override is not proof of the actual
browser timezone; the helper always reports `application_timezone_verified=false`.
Record whether the actual page was checked or still needs refresh/restart.

An exit-IP timezone is a separate geolocation result. Align only with the user's
selected region, and do not promise that changing the OS zone changes IP location
or guarantees a site's device consistency score.

## Resume evidence

New journals may select the region with `automation.py init --timezone IANA_ZONE`.
For an existing journal, use:

```text
python scripts/automation.py set-timezone --state STATE_FILE --timezone CONFIRMED_IANA_ZONE
```

This changes journal intent only, not a remote machine. A changed selection
invalidates timezone, desktop and dependent observations, preserving unrelated
network/pairing evidence. Old journals without the new timezone stage remain
readable but cannot count desktop/acceptance complete until it is observed.

Record the `timezone` stage with `timezone` equal to the selected IANA zone and
the required checks returned by `next`: confirmed datacenter selection, system
readback, observed NTP state, session-override inspection and application-refresh
status. Existing server addresses or personal settings stay in the private
deployment journal; reusable assets contain no preselected server timezone.
