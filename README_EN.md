# 🔑 HomeStream

English | [中文](README.md)

> **We don't build walls, we forge keys.**
>
> We are KeySmiths.
>
> We believe: AI is not a privilege for the few, but a right everyone is born with. The key we forge — zero-cost, running on your own machine, needing no vendor's API — serves one purpose: **to let everyone push open the door and step into their own intelligent new world.**
>
> Every intention bears its consequence. Each forging of this key exists so that the new world of digital intelligence is no longer a private garden for the few, but **a playground for all.**
>
> Open Source Edition V5.0.0 · Self-evolving AI Ecosystem Operating System
>
> Integrate the best of others, forge something new. Follow the natural way, from within to without.

---

<p align="center">
  <strong>MIT Licensed</strong> · <strong>Python 3.9+</strong> · <strong>700+ tests</strong> · <strong>76 API routes</strong>
</p>

---

## What is this?

HomeStream is a lightweight, self-hostable **multi-Agent collaboration framework** — the key to the AI world. It provides:

- 🏠 **Event Hub** — EventStream causal chains, tracing every Agent action to its source
- 💬 **Agent Group Chat** — Channel broadcast, point-to-point messaging, @mention routing, Kanban task callbacks
- 🔐 **Security Built-in** — Token auth, injection defense, log sanitization, rate limiting, three-tier permissions
- 🧠 **Three-tier Model Routing** — L1 local / L2 cloud / L3 backup, auto-failover, **always free fallback**
- 🎯 **Zero-config Startup** — One command to run, progressive upgrade from solo to team
- 🔌 **Elastic Mode** — Solo (single Agent) → Team (multi-Agent collaboration) → Ecosystem (plugin extension)

HomeStream is the open-source cornerstone of the [OpenBridge](https://github.com/Ninefoldatwill/openbridge) ecosystem.

---

## Quick Start

### One-line Install

```bash
# Linux/macOS (GitHub)
curl -fsSL https://raw.githubusercontent.com/Ninefoldatwill/homestream/main/install.sh | bash

# Linux/macOS (Gitee mirror, recommended for China)
curl -fsSL https://gitee.com/ninefoldatwill/homestream/raw/main/install.sh | bash

# Windows PowerShell (GitHub)
iwr -useb https://raw.githubusercontent.com/Ninefoldatwill/homestream/main/install.ps1 | iex

# Windows PowerShell (Gitee mirror, recommended for China)
iwr -useb https://gitee.com/ninefoldatwill/homestream/raw/main/install.ps1 | iex
```

### Manual Install

```bash
# 1. Clone the repository
# GitHub (international)
git clone https://github.com/Ninefoldatwill/homestream.git
# or Gitee (recommended for China, faster)
git clone https://gitee.com/ninefoldatwill/homestream.git
cd homestream

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env to fill in your Agent Token

# 4. Start the service
python bridge_v7_server.py
```

Open your browser to:
- API docs: http://localhost:3458/docs
- Meeting room: http://localhost:3458/meeting
- Health check: http://localhost:3458/health
- Metrics dashboard: http://localhost:3458/metrics

### CLI Tool

```bash
homestream start          # Start service
homestream stop           # Stop service
homestream status         # Check status
homestream mode solo      # Switch to single-Agent mode
homestream mode team      # Switch to team mode
homestream doctor         # Full diagnostics
```

---

## Architecture Overview

```
HomeStream V5
│
├── bridge_v7_server.py       # FastAPI main service (API endpoints)
├── event_stream.py            # EventStream engine (causal chains)
├── event_store.py             # SQLite persistence layer
├── config.py                  # Environment config (.env)
│
├── Security Layer
│   ├── prompt_security.py     # Prompt injection defense
│   ├── permission_guard.py    # Three-tier permission control
│   ├── rate_limiter.py        # Token bucket rate limiting
│   └── log_sanitizer.py       # Log sanitization
│
├── Model Routing
│   ├── model_router.py        # Three-tier routing (L1/L2/L3)
│   └── providers/             # Model provider integrations
│
├── Memory System
│   ├── memory_evolution.py    # Memory evolution (forget/merge/reconstruct)
│   └── soul_config.py         # Soul config (role templates)
│
├── Collaboration Tools
│   ├── skill_router.py        # Skill router
│   ├── worktree_manager.py    # Worktree isolation
│   ├── workflow_engine.py     # Visual workflow engine
│   ├── messaging_gateway.py   # Multi-platform IM gateway
│   └── plugin_registry.py     # Plugin marketplace registry
│
├── CLI Tool
│   └── openbridge/cli.py      # Typer + Rich CLI
│
└── Test Suite
    ├── test_meeting_room.py        # Meeting room integration tests
    ├── test_soul_config.py         # Soul config tests
    ├── test_security_injection.py  # Security injection tests
    └── test_openbridge_cli.py      # CLI tests
```

---

## Loop Engineering

HomeStream practices **Loop Engineering** — tasks run in autonomous loops rather than relying on one-shot prompts.

| Loop Stage | Capability | Module |
|:------------|:-----------|:-------|
| 🔄 **Execute** | Agents autonomously decompose tasks, multi-step serial/parallel | `workflow_engine.py` |
| ✅ **Verify** | Auto-check preconditions before each step | `condition_verifier.py` |
| 🔁 **Retry** | Auto-failover to alternatives on failure, never hard-crash | `failsafe_guardian.py` |
| 📦 **Archive** | Failure lessons auto-recorded, auto-avoided next time | `ratchet_loop.py` |
| 🔍 **Trace** | Trace any step back to root cause via causal chain | `event_stream.py` |
| 🧬 **Learn** | Long-term memory evolution, Agents get smarter over time | `memory_evolution.py` |

> It's not about writing the perfect prompt to get AI right in one shot — it's about designing a "execute → verify → retry → archive → learn" loop that lets AI **spin itself to the right answer.**

---

## Core Concepts

### ICP v1.1 Protocol

9 message types: `INFO` / `ASK` / `TASK` / `UPD` / `DONE` / `WARN` / `ACK` / `PING` / `LOG`

- BLUF (Bottom Line Up Front), single message ≤ 500 characters
- SLA: WARN < 5min / ASK+TASK < 30min

### EventStream Causal Chains

Every Event carries a `cause` field pointing to its upstream trigger Event, forming a complete causal trace chain.

### Elastic Mode — Three Tiers

| Feature | Solo | Team | Ecosystem |
|:--------|:----:|:----:|:---------:|
| EventStream | ✓ | ✓ | ✓ |
| Group Chat | ✓ | ✓ | ✓ |
| Prometheus Monitoring | ✓ | ✓ | ✓ |
| structlog Logging | ✓ | ✓ | ✓ |
| Kanban Task Board | — | ✓ | ✓ |
| Worktree Isolation | — | ✓ | ✓ |
| Ratchet Loop | — | ✓ | ✓ |
| ICP v2 | — | ✓ | ✓ |
| MCP Server | — | — | ✓ |
| A2A Protocol | — | — | ✓ |

### Three-tier Model Routing

| Tier | Model | Latency | Cost | Purpose |
|:----:|:------|:-------:|:----:|:--------|
| L1 | Qwen2.5-7B (local) | ~444ms | Free | Daily reasoning |
| L2 | GLM (cloud) | ~1.4s | Free | Complex tasks |
| L3 | DeepSeek (backup) | ~1.5s | ~$0.001 | Auto-failover |

Dual-line protection: Main line (L1+L2) + Backup line (L3), asyncio.wait_for timeout auto-switch.

---

## API Endpoints

### Event System

| Method | Endpoint | Function |
|:-------|:---------|:---------|
| POST | `/api/v7/events/send` | Send event |
| GET | `/api/v7/events` | Query events |
| GET | `/api/v7/events/chain/{id}` | Causal chain trace |
| GET | `/api/v7/stats` | Statistics |

### Meeting Room

| Method | Endpoint | Function |
|:-------|:---------|:---------|
| POST | `/api/v7/channels/send` | Channel send |
| GET | `/api/v7/channels` | Channel list |
| POST | `/api/v7/callback/kanban` | Kanban callback |
| GET | `/meeting` | Meeting room frontend |

### Tasks & Worktree

| Method | Endpoint | Function |
|:-------|:---------|:---------|
| POST | `/api/v7/tasks/lifecycle` | Task lifecycle |
| POST | `/api/v7/handoff` | Handoff |
| POST | `/api/v7/worktree/create` | Create worktree |
| GET | `/api/v7/worktree/list` | Worktree list |

Full API docs: http://localhost:3458/docs

---

## Security

HomeStream treats security as the first priority:

- **Token Auth** — hmac.compare_digest against timing attacks
- **Injection Defense** — 13 dangerous pattern detection + ICP content filtering
- **Log Sanitization** — Auto-filter token/key/password
- **Rate Limiting** — Token bucket algorithm against abuse
- **Three-tier Permissions** — L1 public / L2 plugin / L3 core graded access

See [SECURITY.md](SECURITY.md)

---

## Testing

```bash
# Run all tests
pytest -v

# Coverage
pytest --cov=. --cov-report=html

# Security scan
bandit -r .
```

Current test status: **700+ tests, 0 failures**

---

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

Quick flow:
1. Fork → 2. Create branch → 3. Develop → 4. Test → 5. PR

---

## Community

- 📖 [Documentation](https://github.com/Ninefoldatwill/homestream/wiki)
- 💬 [Discussions](https://github.com/Ninefoldatwill/homestream/discussions)
- 🐛 [Issue Tracker](https://github.com/Ninefoldatwill/homestream/issues)
- 🇨🇳 [Gitee Mirror](https://gitee.com/ninefoldatwill/homestream) (for China access)
- 📧 contribute@jiuchong.studio

---

## License

MIT License — see [LICENSE](LICENSE)

"HomeStream" is a trademark of JiuChong Studio — see [TRADEMARK.md](TRADEMARK.md)

---

## Acknowledgments

HomeStream's birth would not be possible without the wisdom of the open-source community:

- **FastAPI** — High-performance Python web framework
- **pydantic** — The gold standard for data validation
- **Typer + Rich** — The pinnacle of terminal aesthetics
- **structlog** — Best practices for structured logging
- **Qwen** — Open-source LLM that runs locally
- And all open-source projects contributing to the Agent ecosystem

Integrate the best of others, forge something new. We don't build walls, we forge keys. Together, let everyone push open the door.

---

**JiuChong Studio · KeySmith** · 2026
