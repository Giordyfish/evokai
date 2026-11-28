# Gateway proxy: setting up the PCs

How to put company PCs behind the [gateway proxy](gateway-proxy.md), per system and per tool. The console shows the
same steps with this deployment's values and copy buttons: **Gateways → a gateway → Connect → Company network**.

!!! note "Best effort until tried on your devices"
    The device management paths and the managed keys below come from the vendors' public documentation; they were not
    checked on a managed PC for this release. Try them on one test PC and one test account first, and open the check
    page (`https://evokai.check`) on it.

## What every PC needs

| What | Where it comes from |
|---|---|
| The **PAC file** (automatic proxy configuration): the AI providers' hosts go to the proxy, everything else direct | `{base}/proxy.pac` on the API (needs `EVOKAI_PROXY_PUBLIC_ADDRESS`) |
| The proxy's **root certificate**, in the computer's trusted roots | `{base}/proxy/ca.crt` (PEM), `{base}/proxy/ca.crt?format=der` (DER, for Intune and macOS) |
| For command-line tools, the **proxy address** (`host:port`) | `EVOKAI_PROXY_PUBLIC_ADDRESS` |

The root can sign only for the AI providers' domains and the check host (X.509 Name Constraints): trusting it does not
let the proxy impersonate any other site.

## In this order

1. Distribute the root certificate to the PCs, into the computer's trusted roots.
2. Set the PAC as the PCs' automatic proxy configuration.
3. Configure the tools that do not follow the system: Claude Code, Codex, Firefox. Claude Desktop can also take the
   PAC from its own managed key.
4. Open `https://evokai.check` on a test PC: it must say the PC goes through the proxy, that the certificate is
   trusted, and name the person and gateway it recognised.
5. Only then restrict the providers to Evokai's egress address (the IP allowlists below), so that a PC off the company
   network cannot go around the proxy.

## Windows

### With Intune

1. **The root certificate.** Devices → Configuration → Create → Windows 10 and later → Templates → **Trusted
   certificate**. Upload the DER file and choose the destination store **Computer certificate store - Root**.
2. **The PAC.** A **Settings catalog** profile, category **Network Proxy**: set **Setup Script Url** to the PAC's
   address and turn automatic detection off. With Group Policy, the equivalent is the automatic configuration script
   of the Internet settings.
3. Assign both profiles to devices (not users) and sync a test PC.

Claude Desktop, Edge and Chrome follow the system proxy and the Windows root store. Command-line tools (Claude Code,
Codex) do not: see [the tools](#the-tools).

### A test PC without Intune

In an administrator prompt, with the PEM saved at `C:\ProgramData\Evokai\evokai-proxy-root.crt`:

```bat
certutil -addstore -f Root C:\ProgramData\Evokai\evokai-proxy-root.crt
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v AutoConfigURL /t REG_SZ /d "https://evokai.example.com/proxy.pac" /f
```

The `reg add` sets the PAC for the signed-in user only.

## macOS

### With Intune

1. **The root certificate.** Devices → Configuration → Create → macOS → Templates → **Trusted certificate**: upload
   the DER file. It goes into the System keychain, trusted.
2. **The PAC.** Upload a configuration profile with a proxies payload as a **Custom** profile. The console's
   Company network tab generates one with both payloads, the root read from this deployment:

    ```xml
    <dict>
      <key>PayloadType</key><string>com.apple.security.root</string>
      <key>PayloadContent</key><data><!-- the root, DER, base64 --></data>
      ...
    </dict>
    <dict>
      <key>PayloadType</key><string>com.apple.proxy.http.global</string>
      <key>ProxyType</key><string>Auto</string>
      <key>ProxyPACURL</key><string>https://evokai.example.com/proxy.pac</string>
      <key>ProxyPACFallbackAllowed</key><false/>
      ...
    </dict>
    ```

    Keep only the proxies payload if the certificate comes from the template. `ProxyPACFallbackAllowed` follows the
    deployment's PAC fallback (`false` for `block`).
3. Assign to devices and check on a test Mac, in System Settings → Network → Details → Proxies, that the automatic
   configuration names the PAC.

## Linux

```bash
# The root in the system store: Debian and Ubuntu
sudo cp evokai-proxy-root.crt /usr/local/share/ca-certificates/evokai-proxy-root.crt && sudo update-ca-certificates
# or Fedora, Arch and others with p11-kit
sudo trust anchor --store evokai-proxy-root.crt
# The PAC for the user's desktop (GNOME)
gsettings set org.gnome.system.proxy mode 'auto'
gsettings set org.gnome.system.proxy autoconfig-url 'https://evokai.example.com/proxy.pac'
```

## The tools

### Claude Desktop (Chat, Cowork, Code)

Claude Desktop follows the system proxy. To make sure it uses the PAC whatever the system says, set its optional
managed key `egressProxyPacUrl`:

| System | Where |
|---|---|
| macOS | a Custom settings profile for the preference domain `com.anthropic.claudefordesktop`: `egressProxyPacUrl` (string) |
| Windows | `HKLM\SOFTWARE\Policies\Claude`, string value `egressProxyPacUrl` (Intune script or ADMX-backed setting, or Group Policy) |
| Linux | `/etc/claude-desktop/managed-settings.json`: `{"egressProxyPacUrl": "https://evokai.example.com/proxy.pac"}` |

Avoid `egressProxyUrl` (a fixed proxy): it sends every host to the proxy, and the proxy refuses the hosts outside the
AI catalogue.

### Claude Code

In the managed settings (the managed settings file, or the claude.ai admin settings), with the root saved as PEM on
the PC:

```json
{
  "env": {
    "HTTPS_PROXY": "http://proxy.example.com:3128",
    "NODE_EXTRA_CA_CERTS": "/Library/Application Support/Evokai/evokai-proxy-root.crt"
  }
}
```

Use the path of each system (for example `C:\ProgramData\Evokai\evokai-proxy-root.crt` on Windows,
`/usr/local/share/ca-certificates/evokai-proxy-root.crt` on Linux).

!!! warning "Claude Code sends everything to the proxy"
    With `HTTPS_PROXY`, Claude Code sends all of its HTTPS traffic to the proxy, which refuses hosts outside the AI
    catalogue (403). Web fetches, remote MCP servers and plugin downloads then fail unless their hosts are listed in
    `NO_PROXY`. Where that matters, keep Claude Code on the gateway's base URL door (Connect → Direct address): it needs
    no proxy, and Claude Desktop still goes through the PAC.

### Codex

Codex takes the proxy from `HTTPS_PROXY` and the extra root certificate (the PEM file) from `CODEX_CA_CERTIFICATE`,
else `SSL_CERT_FILE`. Set both as system variables:

```bat
setx /M HTTPS_PROXY http://proxy.example.com:3128
setx /M CODEX_CA_CERTIFICATE C:\ProgramData\Evokai\evokai-proxy-root.crt
```

On macOS and Linux, in the environment of the sessions (a profile script deployed by device management):

```bash
export HTTPS_PROXY=http://proxy.example.com:3128
export CODEX_CA_CERTIFICATE="/Library/Application Support/Evokai/evokai-proxy-root.crt"
```

As for Claude Code, `HTTPS_PROXY` sends all of Codex's HTTPS traffic to the proxy: hosts outside the AI catalogue
(remote MCP servers, for example) need `NO_PROXY`.

### VS Code and GitHub Copilot

VS Code, and the Copilot extension with it, use the system proxy and the system certificates by default. These
settings state it, in case a person changed them (user settings, or VS Code's policies):

```json
{
  "http.proxySupport": "override",
  "http.systemCertificates": true
}
```

GitHub Copilot through the proxy is best effort until its adapters are verified.

### Browsers

Edge, Chrome and Safari use the system proxy and trust the system store. Firefox uses the system proxy by default but
keeps its own trusted roots: its enterprise policy makes it trust the system's (`policies.json`, or the Firefox ADMX
templates with Intune):

```json
{
  "policies": {
    "Certificates": { "ImportEnterpriseRoots": true },
    "Proxy": { "Mode": "system" }
  }
}
```

## Make it the only way in: the providers' IP allowlists

A PC off the company network, or without the PAC, could reach the providers directly. Where the provider offers it,
accept the organisation's accounts only from Evokai's egress address (`EVOKAI_PROXY_EGRESS_IPS`, shown in the
console):

| Provider | Where |
|---|---|
| Claude Enterprise | the organisation's settings, IP allowlisting (beta, organisation owners only) |
| ChatGPT Enterprise and Edu | Workspace Settings → Identity & access → IP allowlist |
| GitHub Enterprise Cloud | the organisation's Settings → Authentication security → IP allow list |

An allowlist applies to every way into the account, not only to the AI apps: add the office's own addresses too where
people use the providers' websites or Git directly (GitHub's list covers Git, the API and the website). An allowlist
does not stop personal accounts: set the proxy's allowed organisations for that (console, **Gateway proxy**).

## When the proxy cannot be reached

The PAC's fallback is the deployment's `EVOKAI_PROXY_PAC_FALLBACK`:

- `block` (the default): the PAC names only the proxy. With the proxy down the AI apps cannot connect; no request goes
  around the policy.
- `direct`: with the proxy down PCs go straight to the providers, without checks and records. With an IP allowlist
  those requests are refused anyway.

## Check a PC

Open `https://evokai.check` on the PC. The proxy answers it itself: whether the PC goes through the proxy, whether
its certificate is trusted, and the person and gateway it recognised from that PC lately. Without the proxy (no PAC,
or the proxy down) the page does not open, since `evokai.check` is not a real domain.
