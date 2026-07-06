# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 5.x     | :white_check_mark: |
| < 5.0   | :x:                |

## Reporting a Vulnerability

We take security seriously at HomeStream. If you discover a security vulnerability, please **do NOT** file a public issue.

Instead, report it privately via:

- **Email**: security@jiuchong.studio
- **GitHub Security Advisory**: Use the "Report a vulnerability" button on the Security tab

We will respond within 48 hours and work with you on a coordinated disclosure timeline.

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Security Best Practices for HomeStream Users

1. **Always use HTTPS** in production deployments
2. **Rotate Agent tokens** regularly (at least every 90 days)
3. **Never hardcode tokens** in source code — use `.env` files
4. **Review ICP message content** before broadcasting to channels
5. **Enable structured logging** with log sanitization enabled

## Security Features

HomeStream includes these built-in security mechanisms:

- **Token-based authentication** with hmac.compare_digest timing-attack resistance
- **ICP injection protection** — filters dangerous patterns in inter-agent messages
- **Log sanitization** — automatically redacts tokens, API keys, and passwords from logs
- **Rate limiting** — token bucket algorithm to prevent abuse
- **Permission guard** — three-tier access control (L1 public / L2 plugin / L3 core)

## Acknowledgments

We appreciate the security research community. Contributors who responsibly disclose vulnerabilities will be acknowledged here (with permission).
