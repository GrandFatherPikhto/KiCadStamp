#!/usr/bin/env python3
"""
dump_chat_history.py — выгружает историю разговоров Claude Code по этому
проекту в читаемый Markdown: один файл на сессию плюс оглавление.

Источник — транскрипты Claude Code, `~/.claude/projects/<slug>/*.jsonl`, где
<slug> — абсолютный путь к репозиторию с '/' (и '.') заменёнными на '-'.
Берётся ТОЛЬКО текст разговора:

  * сообщения Дениса (без служебных вставок: <system-reminder>, открытые в IDE
    файлы, диагностики, обёртки slash-команд);
  * ответы Claude (текстовые блоки; размышления и вызовы инструментов не
    выгружаются — вместо вызовов одна строка «⚙ Bash ×3, Edit»);
  * вставки (<pasted_content>) — цитатой с пометкой «Вставка»;
  * пересказы при сжатии контекста — свёрнутым блоком <details>.

Выводы команд, содержимое файлов, картинки и прочие служебные записи
пропускаются: архив для чтения человеком, не резервная копия.

Только чтение транскриптов, никакой сети, никакой модели. Повторный запуск
перезаписывает выходные файлы (снимок «на сейчас»). Каталог по умолчанию —
techdocs/chat_history/ (techdocs в .gitignore: в истории есть личное — почта,
адреса машин, пути).

Запуск (из корня репозитория, как и остальные скрипты в tools/):
  python tools/dump_chat_history.py
  python tools/dump_chat_history.py --out ~/Documents/kicadstamp_chats
  python tools/dump_chat_history.py --source ~/.claude/projects/<slug>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

USER_LABEL = "Денис"
ASSISTANT_LABEL = "Claude"

# Service wrappers the harness injects into a user turn — not words of the user.
_DROP_TAGS = (
    "system-reminder", "ide_opened_file", "ide_diagnostics", "ide_selection",
    "local-command-caveat", "local-command-stdout", "local-command-stderr",
    "command-name", "command-message", "command-args", "task-notification",
)
_DROP_RE = re.compile(
    r"<(%s)\b[^>]*>.*?</\1>" % "|".join(map(re.escape, _DROP_TAGS)), re.S)
_PASTE_RE = re.compile(r"<pasted_content\b[^>]*>(.*?)</pasted_content\b[^>]*>", re.S)
_NOTICE_PREFIXES = ("[SYSTEM NOTIFICATION", "Tool loaded.")


def default_source() -> Path:
    """~/.claude/projects/<slug> for this repository."""
    slug = re.sub(r"[/.]", "-", str(REPO_ROOT))
    return Path.home() / ".claude" / "projects" / slug


def _local_time(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _quote(text: str) -> str:
    return "\n".join("> " + line if line else ">" for line in text.splitlines())


def clean_user_text(text: str) -> str:
    """User text without the harness's service blocks; pastes become quotes."""
    text = _DROP_RE.sub("", text)

    def paste(m: re.Match) -> str:
        body = m.group(1).strip("\n")
        return "\n\n**Вставка:**\n\n" + _quote(body) + "\n\n"

    text = _PASTE_RE.sub(paste, text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if text.startswith(_NOTICE_PREFIXES):
        return ""
    return text


def _blocks(content) -> list:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in (content or []) if isinstance(b, dict)]


def _tools_line(tools: Counter) -> str:
    parts = [f"{name} ×{n}" if n > 1 else name for name, n in tools.items()]
    return "_⚙ " + ", ".join(parts) + "_"


class Session:
    """One transcript file folded into speaker turns."""

    def __init__(self, path: Path):
        self.path = path
        self.title: str | None = None
        self.turns: list[dict] = []   # {who, when, parts[list[str]]}
        self._seen: set = set()
        self._tools: Counter = Counter()

    # ── building ──────────────────────────────────────────────────────────
    def _turn(self, who: str, when) -> dict:
        if not self.turns or self.turns[-1]["who"] != who:
            self.turns.append({"who": who, "when": when, "parts": []})
        return self.turns[-1]

    def _flush_tools(self) -> None:
        if self._tools and self.turns and self.turns[-1]["who"] == ASSISTANT_LABEL:
            self.turns[-1]["parts"].append(_tools_line(self._tools))
        self._tools = Counter()

    def add(self, rec: dict) -> None:
        kind = rec.get("type")
        if kind == "ai-title" and rec.get("aiTitle"):
            self.title = rec["aiTitle"]
            return
        if kind not in ("user", "assistant") or rec.get("isSidechain"):
            return
        if rec.get("isMeta") or rec.get("isApiErrorMessage"):
            return
        key = rec.get("uuid")
        if key is not None:
            if key in self._seen:
                return
            self._seen.add(key)
        when = _local_time(rec.get("timestamp"))
        content = (rec.get("message") or {}).get("content")
        if kind == "user":
            self._add_user(rec, content, when)
        else:
            self._add_assistant(content, when)

    def _add_user(self, rec: dict, content, when) -> None:
        if rec.get("isCompactSummary"):
            self._flush_tools()
            text = _DROP_RE.sub("", "\n".join(
                b.get("text", "") for b in _blocks(content)
                if b.get("type") == "text")).strip()
            if text:
                self._turn("compact", when)["parts"].append(text)
            return
        texts, images = [], 0
        for b in _blocks(content):
            if b.get("type") == "text":
                cleaned = clean_user_text(b.get("text", ""))
                if cleaned:
                    texts.append(cleaned)
            elif b.get("type") == "image":
                images += 1
        if images:
            texts.append(f"_[картинка ×{images}]_" if images > 1 else "_[картинка]_")
        if not texts:
            return   # a tool result or a pure service turn
        self._flush_tools()
        self._turn(USER_LABEL, when)["parts"].extend(texts)

    def _add_assistant(self, content, when) -> None:
        for b in _blocks(content):
            if b.get("type") == "tool_use":
                self._tools[b.get("name") or "?"] += 1
                if not self.turns or self.turns[-1]["who"] != ASSISTANT_LABEL:
                    self._turn(ASSISTANT_LABEL, when)
            elif b.get("type") == "text" and b.get("text", "").strip():
                turn = self._turn(ASSISTANT_LABEL, when)
                if self._tools:
                    turn["parts"].append(_tools_line(self._tools))
                    self._tools = Counter()
                turn["parts"].append(b["text"].strip())

    def finish(self) -> None:
        self._flush_tools()
        self.turns = [t for t in self.turns if t["parts"]]

    # ── output ────────────────────────────────────────────────────────────
    @property
    def started(self) -> datetime | None:
        return next((t["when"] for t in self.turns if t["when"]), None)

    @property
    def ended(self) -> datetime | None:
        return next((t["when"] for t in reversed(self.turns) if t["when"]), None)

    def file_name(self) -> str:
        day = self.started.strftime("%Y-%m-%d") if self.started else "undated"
        return f"{day}_{self.path.stem[:8]}.md"

    def heading(self) -> str:
        return self.title or self.path.stem

    def to_markdown(self) -> str:
        out = [f"# {self.heading()}", "",
               f"Сессия `{self.path.stem}` · {_span(self.started, self.ended)}", ""]
        for t in self.turns:
            stamp = t["when"].strftime("%Y-%m-%d %H:%M") if t["when"] else ""
            if t["who"] == "compact":
                out += ["<details><summary>Сжатие контекста · " + stamp
                        + "</summary>", "", *t["parts"], "", "</details>", ""]
                continue
            out += [f"## {t['who']} · {stamp}".rstrip(" ·"), ""]
            for part in t["parts"]:
                out += [part, ""]
        return "\n".join(out).rstrip() + "\n"


def _span(a: datetime | None, b: datetime | None) -> str:
    if a is None:
        return "без дат"
    first = a.strftime("%Y-%m-%d %H:%M")
    if b is None or b == a:
        return first
    last = b.strftime("%H:%M") if b.date() == a.date() else b.strftime("%Y-%m-%d %H:%M")
    return f"{first} — {last}"


def read_session(path: Path) -> Session:
    session = Session(path)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue   # a torn last line of a live session
            if isinstance(rec, dict):
                session.add(rec)
    session.finish()
    return session


def write_index(out_dir: Path, sessions: list[Session]) -> Path:
    lines = ["# История разговоров с Claude", "",
             f"Выгружено {datetime.now().astimezone():%Y-%m-%d %H:%M} скриптом "
             "`tools/dump_chat_history.py`. Только текст разговора: без выводов "
             "команд, содержимого файлов и размышлений.", "",
             "| начало | конец | реплик | сессия |", "|---|---|---|---|"]
    for s in sessions:
        a = s.started.strftime("%Y-%m-%d %H:%M") if s.started else "—"
        b = s.ended.strftime("%Y-%m-%d %H:%M") if s.ended else "—"
        title = s.heading().replace("|", "\\|")
        lines.append(f"| {a} | {b} | {len(s.turns)} | [{title}]({s.file_name()}) |")
    path = out_dir / "README.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    ap.add_argument("--source", type=Path, default=None,
                    help="каталог с *.jsonl (по умолчанию ~/.claude/projects/<slug> этого репо)")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "techdocs" / "chat_history",
                    help="куда писать Markdown (по умолчанию techdocs/chat_history/)")
    args = ap.parse_args(argv)

    source = (args.source or default_source()).expanduser()
    files = sorted(source.glob("*.jsonl"))
    if not files:
        print(f"нет транскриптов в {source}", file=sys.stderr)
        return 1
    out_dir = args.out.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    sessions = [s for s in (read_session(f) for f in files) if s.turns]
    sessions.sort(key=lambda s: s.started or datetime.min.astimezone())
    total = 0
    for s in sessions:
        text = s.to_markdown()
        (out_dir / s.file_name()).write_text(text, encoding="utf-8")
        total += len(text)
        print(f"{s.file_name()}: {len(s.turns)} реплик, {len(text) / 1e6:.1f} M символов")
    index = write_index(out_dir, sessions)
    print(f"{len(sessions)} сессий, {total / 1e6:.1f} M символов → {out_dir} ({index.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
