# RCA: Day 47 - SSRF DNS Multi-Address Validation Bypass & Null-Byte Path Sanitization

- **Date**: 2026-09-09
- **Trigger**: During Day 47 OWASP API Security Top 10 hardening and security test suite validation:
  1. Potential SSRF firewall bypass via dual-stack / multi-record DNS hostnames resolving to both public and private IP addresses.
  2. Null-byte truncation vulnerability (`filename.txt\x00.png`) and mixed-slash directory traversal (`..\` vs `../`) in file download endpoint parameter sanitization.
  3. Starlette / FastAPI CORS middleware failure when combining wildcard origin (`allow_origins=["*"]`) with session credential support (`allow_credentials=True`).

- **Faulty Code / Pattern**:
  ```python
  # FLAW 1: Naively validating only the first DNS address returned
  def validate_safe_url(url: str) -> str:
      parsed = urlparse(url)
      # getaddrinfo returns multiple addr tuples for dual-homed or round-robin DNS:
      addr_info = socket.getaddrinfo(parsed.hostname, port)
      primary_ip = addr_info[0][4][0]  # Only checks first IP!
      if is_private_or_loopback(primary_ip):
          raise SSRFSecurityException()
      # ATTACK: Attacker registers domain resolving to [1.1.1.1, 127.0.0.1].
      # First entry passes check; subsequent client request hits internal 127.0.0.1!

  # FLAW 2: Incomplete regex or string replacement for path traversal
  def sanitize_file_path(filename: str) -> str:
      # Naively replaces only POSIX '../':
      clean_name = filename.replace("../", "")
      # ATTACK 1: Windows backslash traversal '..\' escapes intact!
      # ATTACK 2: Null-byte injection 'malicious.py\x00.jpg' truncates string in C file APIs!
      return clean_name

  # FLAW 3: Wildcard CORS with credentials enabled
  app.add_middleware(
      CORSMiddleware,
      allow_origins=["*"],
      allow_credentials=True,  # Insecure & rejected by modern browsers / ASGI middleware!
  )
  ```

- **Root Cause**:
  1. **DNS Multi-A/AAAA Record Rebinding & Asymmetric Resolution**:
     Hostnames can resolve to multiple IP addresses (e.g. IPv4 + IPv6, or round-robin DNS pools). If an SSRF validation firewall only inspects `addr_info[0]`, an adversary can configure a malicious domain with a public IP as the first DNS record and a private loopback/metadata IP (`169.254.169.254`) as the second record. When the HTTP client library connects, it may connect to the second address, bypassing the firewall.
  2. **Null-Byte Termination & Multi-Platform Directory Separators**:
     In legacy POSIX and C-based filesystems, byte `0x00` acts as a string null terminator. Attackers inject `secret_config.json\x00.png` to satisfy naive `.endswith(".png")` checks while opening `secret_config.json`. Furthermore, Windows environments support both forward slashes (`/`) and backslashes (`\`), allowing `..\` to bypass POSIX-only sanitizers.
  3. **CORS Security Specification Invariant**:
     Per the W3C CORS specification and OWASP recommendations, browsers strictly disallow requests containing `Access-Control-Allow-Origin: *` when credentials (cookies, HTTP basic auth, client certificates) are included (`allow_credentials=True`).

- **Resolution**:
  ```python
  # 1. Comprehensive All-Address Resolution Check (app/core/ssrf_protection.py)
  def validate_safe_url(url: str) -> str:
      parsed = urlsplit(url)
      if parsed.scheme.lower() not in {"http", "https"}:
          raise SSRFSecurityException(f"Forbidden protocol scheme: {parsed.scheme}")
      
      hostname = parsed.hostname
      if not hostname:
          raise SSRFSecurityException("URL missing valid hostname")

      # Resolve ALL socket addresses across IPv4 and IPv6
      addr_info = socket.getaddrinfo(hostname, None, family=socket.AF_UNSPEC)
      
      for entry in addr_info:
          ip_str = entry[4][0]
          target_ip = ip_address(ip_str)
          
          # Inspect target against ALL forbidden CIDR ranges (O(1) bitwise subnet checks)
          for forbidden_net in FORBIDDEN_IP_NETWORKS:
              if target_ip in forbidden_net:
                  raise SSRFSecurityException(
                      f"Destination IP {ip_str} resolves to restricted CIDR {forbidden_net}"
                  )
      return url

  # 2. Canonical Basename & Null-Byte Rejection (app/core/sanitization.py)
  def sanitize_file_path(filename: str, strict: bool = False) -> str:
      if "\x00" in filename:
          raise SecurityException("Null byte detected in file path")
      
      # Strip all control characters (0-31 and 127)
      sanitized = "".join(ch for ch in filename if ord(ch) >= 32 and ord(ch) != 127)
      
      # Extract canonical safe basename using both POSIX and Windows separators
      sanitized = os.path.basename(sanitized.replace("\\", "/"))
      sanitized = re.sub(r"\.\.+", "", sanitized)
      
      if not sanitized:
          raise SecurityException("Filename resolved to empty after sanitization")
      return sanitized

  # 3. Explicit Production CORS Whitelist (app/core/config.py & app/main.py)
  # Permanent rule: NEVER use ["*"] with allow_credentials=True
  app.add_middleware(
      CORSMiddleware,
      allow_origins=settings.allowed_cors_origins,  # Explicit domain whitelist
      allow_credentials=True,
      allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
      allow_headers=["*"],
  )
  ```

- **Permanent Prevention Rules**:
  - *Rule 87*: In SSRF validation firewalls, iterate over *every* resolved IP address returned by `socket.getaddrinfo()`; if any destination address falls within a private, loopback, or cloud-metadata CIDR range, reject the request.
  - *Rule 88*: When sanitizing file paths, unconditionally reject null bytes (`\x00`), strip control characters, normalize backslashes (`\`) to forward slashes (`/`), and use `os.path.basename()` to eliminate directory traversal.
  - *Rule 89*: Never configure CORS with wildcard origins (`*`) when `allow_credentials=True`.
