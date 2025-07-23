# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Essential Commands

**Running the Application:**
```bash
# Development run
python app.py

# Production background run (with log monitoring)
./scripts/start.sh

# Monitor logs
./tail_log.sh  # Monitors wechat_robot.log with last 20 lines
tail -f nohup.out  # For background runs
```

**Code Quality:**
```bash
# Install pre-commit hooks (recommended)
pre-commit install

# Run all quality checks
pre-commit run --all-files

# Individual tools
black .                # Code formatting
isort .                # Import sorting
flake8 .               # Linting

# Run tests
python -m pytest tests/
python -m unittest tests/test_utils.py  # Single test file
```

**Dependencies:**
```bash
# Core dependencies
pip install -r requirements.txt

# Optional features (voice, additional models, etc.)
pip install -r requirements-optional.txt
```

## Architecture Overview

This is a **multi-AI chatbot framework** that supports 20+ AI models across 10+ communication channels using a **Bridge pattern** for decoupling.

### Core Architecture Layers

**Bridge Pattern (Central):**
- `bridge/bridge.py` - Central routing and model selection logic
- `bridge/context.py` - Request/response context management
- `bridge/reply.py` - Response formatting and handling

**Bot Layer (AI Models):**
- `bot/bot_factory.py` - Factory pattern for bot creation
- `bot/session_manager.py` - Conversation state management
- Individual bot implementations in `bot/{provider}/` directories
- Model routing logic in `bridge.py` based on `config.json` model field

**Channel Layer (Communication Platforms):**
- `channel/channel_factory.py` - Factory pattern for channel creation
- `channel/chat_channel.py` - Base chat functionality
- Individual channel implementations in `channel/{platform}/` directories

**Plugin System:**
- `plugins/plugin_manager.py` - Plugin lifecycle management
- Individual plugins in `plugins/{name}/` directories
- Loaded automatically for most channel types (see `app.py:34-36`)

### Key Design Patterns

**Factory Pattern:** Both bots and channels use factories for dynamic instantiation based on configuration.

**Session Management:** Each user conversation maintains state through `bot/session_manager.py` with configurable TTL.

**Configuration-Driven Routing:** The `bridge/bridge.py` contains complex model routing logic that maps configuration model names to specific bot implementations.

**Plugin Architecture:** Extensible plugin system that hooks into message processing pipeline.

## Configuration System

**Primary Config:** `config.json` (copy from `config-template.json`)

**Key Configuration Sections:**
- `channel_type`: Determines which communication platform to use
- `model`: AI model identifier that drives bot selection in bridge routing
- `*_api_key`: Various API keys for different AI services
- `single_chat_prefix`/`group_chat_prefix`: Message trigger patterns
- `tmp_cleanup_*`: Temporary file management settings

**Model Routing Logic:** Located in `bridge/bridge.py:27-80`, maps model names to bot types:
- OpenAI models → `const.OPEN_AI` or `const.CHATGPT`
- Gemini models → `const.GEMINI`
- Baidu models → `const.BAIDU`
- And 15+ other providers

## Development Guidelines

**Code Style:**
- Black formatting with 176 character line length
- Isort for import organization
- Flake8 linting (limited rule set, see `.flake8`)
- Pre-commit hooks enforce all style rules

**Testing:**
- Tests located in `tests/` directory
- Use unittest framework
- Current test coverage focuses on utility functions

**Adding New AI Models:**
1. Create bot class in `bot/{provider}/{provider}_bot.py`
2. Add model routing logic in `bridge/bridge.py`
3. Add constants in `common/const.py`
4. Register in `bot/bot_factory.py`

**Adding New Channels:**
1. Create channel class in `channel/{platform}/{platform}_channel.py`
2. Create message class in `channel/{platform}/{platform}_message.py`
3. Register in `channel/channel_factory.py`
4. Add channel type constant

**Key Files to Understand:**
- `app.py` - Application entry point and channel startup
- `bridge/bridge.py` - Core routing and model selection (most complex file)
- `bot/bot_factory.py` - Bot instantiation logic
- `channel/channel_factory.py` - Channel instantiation logic
- `config.py` - Configuration loading and management

**External Libraries:**
- Custom itchat implementation in `lib/itchat/`
- WxPad client in `lib/wxpad/`
- Dify integration in `lib/dify/`
- Avoid modifying lib/ contents (excluded from linting/formatting)

**Memory Management:**
- Temporary file cleanup system enabled by default
- Session management with configurable expiration
- Plugin loading only for specific channel types to optimize memory