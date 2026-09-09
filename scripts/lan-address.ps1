# This machine's address on the local network, for testing on a phone.
#
# Dot-sourced by dev-api.ps1 and dev-web.ps1 so both print the same URL:
#
#     . (Join-Path $PSScriptRoot "lan-address.ps1")
#     $lan = Get-LanAddress
#
# Returns $null rather than failing when there is no wifi — the scripts
# still work on the laptop alone, they just have nothing extra to print.

function Get-LanAddress {
    try {
        $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop
    }
    catch {
        return $null
    }
    # The three private ranges (RFC 1918), which is what a router hands
    # out. Ordered so an ordinary home network (192.168.x.x) wins over a
    # virtual adapter from Docker or WSL, which usually sits in 172.x.
    foreach ($pattern in '^192\.168\.', '^10\.', '^172\.(1[6-9]|2\d|3[01])\.') {
        $match = $addresses |
            Where-Object { $_.IPAddress -match $pattern } |
            Select-Object -First 1
        if ($match) { return $match.IPAddress }
    }
    return $null
}
