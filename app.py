"""A small Windows desktop companion. Run with --demo for a labelled UI preview."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import html
import os
from pathlib import Path
import sys
import tempfile
import threading
import uuid

from PySide6.QtCore import (QAbstractNativeEventFilter, QByteArray, QBuffer, QIODevice,
                            QObject, QPoint, QRect, QTimer, Qt, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QImage, QKeySequence,
                           QPainter, QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QKeySequenceEdit, QLabel,
    QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea, QSizeGrip, QSlider, QSpinBox,
    QStackedWidget, QSystemTrayIcon, QTabWidget, QTextEdit, QVBoxLayout, QWidget)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtTextToSpeech import QTextToSpeech

from core import (Cancelled, CompanionError, LocalAI, config_path, hotkey_codes,
                  load_settings, local_url, save_settings, speech_text, synthesize, youtube_url)
from chat_format import ChatBrowser
from game_guides import BUILD_WORDS
from web_search import clear_legacy_guide_cache, public_url
from web_tools import ToolError, searxng_url, search_searxng

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "assets" / "raiden.png"

STYLE = """
QWidget { color: #eee8fb; font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 13px; }
QFrame#shell { background: #171322; border: 1px solid #4c3c65; border-radius: 22px; }
QDialog { background: #171322; }
QLabel#eyebrow { color: #c8abf2; font-size: 10px; font-weight: 700; letter-spacing: 2px; }
QLabel#title { color: #fcf7ff; font-size: 26px; font-weight: 600; }
QLabel#subtitle, QLabel#hint { color: #b4a8c6; font-size: 12px; }
QLabel#status { color: #c9b5e9; font-size: 11px; }
QPushButton { background: #30253f; border: 1px solid #56416e; border-radius: 10px;
 padding: 9px 12px; text-align: center; }
QPushButton:hover { background: #443056; border-color: #b997e4; }
QPushButton:pressed { background: #604077; }
QPushButton:disabled { color: #71667f; background: #211b2c; border-color: #352b43; }
QPushButton#primary { background: #b99ae9; color: #1c122b; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #d5b7ff; }
QPushButton#quiet { border: none; background: transparent; color: #bbadcc; padding: 6px 9px; }
QPushButton#card { background: #241c32; border: 1px solid #514067; border-radius: 15px;
 text-align: left; padding: 19px; font-size: 14px; }
QPushButton#card:hover { background: #352443; border-color: #c4a5ef; }
QTextEdit, QLineEdit, QTextBrowser, QSpinBox, QComboBox, QKeySequenceEdit {
 background: #211b2d; color: #f1eafb; border: 1px solid #4e3e61; border-radius: 9px; padding: 8px;
 selection-background-color: #684788; }
QTextBrowser { border: none; background: #171322; padding: 1px; }
QComboBox QAbstractItemView { background: #282033; color: #f1eafb; selection-background-color: #684788; }
QCheckBox { padding: 4px 0; }
QTabWidget::pane { border: 1px solid #493954; border-radius: 8px; padding: 8px; }
QTabBar::tab { background: #241c32; padding: 10px 14px; }
QTabBar::tab:selected { background: #513766; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #211b2d; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #66517f; min-height: 28px; border-radius: 3px; }
QScrollBar::handle:vertical:hover { background: #987cba; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QWidget#settingsPage, QWidget#settingsViewport { background: #171322; }
QSlider::groove:horizontal { height: 5px; background: #42304e; border-radius: 2px; }
QSlider::handle:horizontal { width: 16px; height: 16px; margin: -6px 0; background: #c7a5f4; border-radius: 8px; }
QToolTip { background: #30253f; color: #fff; border: 1px solid #79608f; padding: 6px; }
QMenu { background: #211b2d; color: #eee8fb; border: 1px solid #614976; padding: 5px; }
QMenu::item { padding: 7px 18px; } QMenu::item:selected { background: #513766; }
"""


def button(text, handler=None, style="", tip=""):
    item = QPushButton(text)
    if handler:
        item.clicked.connect(handler)
    if style:
        item.setObjectName(style)
    if tip:
        item.setToolTip(tip)
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    return item


class Jobs(QObject):
    done = Signal(int, str, object)
    failed = Signal(int, str, str)
    progress = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.counter = 0
        self.cancel_events = {}

    def start(self, kind, fn):
        self.counter += 1
        number = self.counter
        event = threading.Event()
        self.cancel_events[number] = event

        def run():
            try:
                result = fn(lambda text: self.progress.emit(number, text), event.is_set)
                if not event.is_set():
                    self.done.emit(number, kind, result)
            except Cancelled:
                pass
            except Exception as exc:
                if not event.is_set():
                    message = str(exc) if isinstance(exc, CompanionError) else "An unexpected error occurred. Check the service configuration and try again."
                    self.failed.emit(number, kind, message)
            finally:
                self.cancel_events.pop(number, None)

        threading.Thread(target=run, daemon=True).start()
        return number

    def cancel(self, number):
        event = self.cancel_events.get(number)
        if event:
            event.set()


class NativeHotkey(QAbstractNativeEventFilter):
    def __init__(self, callback, target):
        super().__init__()
        self.callback, self.target = callback, target
        self.current_id, self.sequence = None, None
        self.shortcut = None
        QApplication.instance().installNativeEventFilter(self)

    def apply(self, sequence):
        sequence = sequence.strip()
        if self.sequence == sequence:
            return
        if not sequence:
            self.close()
            self.sequence = ""
            return
        modifiers, key = hotkey_codes(sequence)
        if sys.platform == "win32":
            new_id = 2 if self.current_id == 1 else 1
            if not ctypes.windll.user32.RegisterHotKey(None, new_id, modifiers, key):
                raise CompanionError("That hotkey is already in use or reserved. Choose another combination.")
            if self.current_id is not None:
                ctypes.windll.user32.UnregisterHotKey(None, self.current_id)
            self.current_id = new_id
        else:
            if self.shortcut:
                self.shortcut.setEnabled(False)
                self.shortcut.deleteLater()
            self.shortcut = QShortcut(QKeySequence(sequence), self.target)
            self.shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            self.shortcut.activated.connect(self.callback)
        self.sequence = sequence

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32" and bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == self.current_id:
                QTimer.singleShot(0, self.callback)
                return True, 0
        return False, 0

    def close(self):
        if sys.platform == "win32" and self.current_id is not None:
            ctypes.windll.user32.UnregisterHotKey(None, self.current_id)
        self.current_id = None
        if self.shortcut:
            self.shortcut.setEnabled(False)


class Mascot(QWidget):
    clicked = Signal()
    moved = Signal()
    context_requested = Signal(QPoint)

    def __init__(self, settings):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Raiden Companion")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.offset = None
        self.dragged = False
        self.apply(settings)

    def apply(self, settings):
        pixmap = QPixmap(settings.get("image_path") or str(ICON))
        if pixmap.isNull():
            pixmap = QPixmap(str(ICON))
        size = settings["mascot_size"]
        self.pixmap = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.setFixedSize(self.pixmap.size())
        self.setMask(self.pixmap.mask())
        self.setWindowOpacity(settings["opacity"])
        self.setToolTip("Click to chat • Drag to move • Right-click for settings\n" + (settings["hotkey"] or "Hotkey disabled"))
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.pixmap)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.offset = event.globalPosition().toPoint() - self.pos()
            self.start_pos = event.globalPosition().toPoint()
            self.dragged = False
        elif event.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if self.offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.globalPosition().toPoint() - self.start_pos).manhattanLength() > 6:
                self.dragged = True
                self.move(event.globalPosition().toPoint() - self.offset)
                self.moved.emit()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.offset is not None:
            self.offset = None
            if not self.dragged:
                self.clicked.emit()


class SettingsDialog(QDialog):
    def __init__(self, companion):
        super().__init__(companion.panel)
        self.companion = companion
        self.values = companion.settings.copy()
        self.setWindowTitle("Raiden · Settings · 0.14")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(540, 590)
        root = QVBoxLayout(self)
        title = QLabel("Make her yours")
        title.setObjectName("title")
        root.addWidget(title)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)
        appearance, model, voice, guides = [QWidget() for _ in range(4)]
        for widget, label in [(appearance, "Appearance"), (model, "AI"), (voice, "Voice"), (guides, "Guides")]:
            widget.setObjectName("settingsPage")
            scroll = QScrollArea()
            scroll.viewport().setObjectName("settingsViewport")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(widget)
            tabs.addTab(scroll, label)

        form = QFormLayout(appearance)
        self.image = QLineEdit(self.values["image_path"])
        self.image.setPlaceholderText("Built-in chibi Raiden")
        form.addRow("Icon image", self.image)
        browse = QHBoxLayout()
        browse.addWidget(button("Choose image…", self.pick_image))
        browse.addWidget(button("Use Raiden", lambda: self.image.clear(), "quiet"))
        form.addRow(browse)
        self.icon_size = QSpinBox()
        self.icon_size.setRange(90, 220)
        self.icon_size.setSuffix(" px")
        self.icon_size.setValue(self.values["mascot_size"])
        form.addRow("Icon size", self.icon_size)
        opacity_row = QHBoxLayout()
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(round(self.values["opacity"] * 100))
        self.opacity_label = QLabel(f"{self.opacity.value()}%")
        self.opacity.valueChanged.connect(lambda n: self.opacity_label.setText(f"{n}%"))
        opacity_row.addWidget(self.opacity)
        opacity_row.addWidget(self.opacity_label)
        form.addRow("Icon opacity", opacity_row)
        self.hotkey = QKeySequenceEdit(QKeySequence(self.values["hotkey"]))
        self.hotkey.setMaximumSequenceLength(1)
        self.hotkey.setClearButtonEnabled(True)
        form.addRow("Open / close", self.hotkey)
        hint = QLabel("Click the shortcut field, then press your combination.\nDefault: Ctrl+Alt+R. Clear it to disable.\nAt 0% opacity, use the hotkey or tray icon to reopen Settings.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        form.addRow(hint)
        form.addRow(button("Move icon back to corner", companion.reset_position, "quiet"))
        form.addRow(button("Reset chat window size", companion.panel.reset_size, "quiet"))
        if sys.platform != "win32":
            form.addRow(QLabel("Global hotkeys are implemented for Windows.\nOn this OS, the shortcut works while the app has focus."))

        form = QFormLayout(model)
        self.base = QLineEdit(self.values["base_url"])
        self.model = QLineEdit(self.values["model"])
        self.model.setPlaceholderText("Blank = choose from the local server")
        self.local_key = QLineEdit(self.values["local_api_key"])
        self.local_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.local_key.setPlaceholderText("Only if your local server requires it")
        form.addRow("Local API", self.base)
        form.addRow("Model ID", self.model)
        form.addRow("API token", self.local_key)
        form.addRow(button("LM Studio defaults", lambda: self.base.setText("http://127.0.0.1:1234/v1"), "quiet"))
        form.addRow(button("Ollama defaults", lambda: self.base.setText("http://127.0.0.1:11434/v1"), "quiet"))
        self.fast_replies = QCheckBox("Fast Qwen replies (request Thinking off)")
        self.fast_replies.setChecked(self.values["qwen_fast_replies"])
        self.fast_replies.setToolTip("Requests direct answers for Qwen3.5. Turn this off to use the server's reasoning settings. Other model families are unaffected.")
        form.addRow(self.fast_replies)
        self.ai_timeout = QSpinBox()
        self.ai_timeout.setRange(30, 1800)
        self.ai_timeout.setSingleStep(30)
        self.ai_timeout.setSuffix(" seconds")
        self.ai_timeout.setValue(self.values["ai_timeout"])
        form.addRow("Reply timeout", self.ai_timeout)
        hint = QLabel("In Bionic, start Settings → Local Model API. Use a loaded Qwen3.5 vision model. Fast replies requests Thinking off for both text and screenshots. The longer timeout allows slower laptops more time to answer.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        form.addRow(hint)
        self.language = QComboBox()
        self.language.addItem("English", "en")
        self.language.addItem("Japanese", "ja")
        self.language.setCurrentIndex(self.language.findData(self.values["reply_language"]))
        form.addRow("Reply language", self.language)
        form.addRow(button("Open setup guide", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT / "START-HERE.html"))), "quiet"))

        form = QFormLayout(voice)
        self.voice = QComboBox()
        for label, value in [("System voice (temporary)", "system"), ("Character voice · GPT-SoVITS", "gpt_sovits"), ("Off", "off")]:
            self.voice.addItem(label, value)
        self.voice.setCurrentIndex(self.voice.findData(self.values["voice"]))
        form.addRow("Speech engine", self.voice)
        self.auto = QCheckBox("Read replies automatically")
        self.auto.setChecked(self.values["auto_speak"])
        form.addRow(self.auto)
        self.tts = QLineEdit(self.values["tts_url"])
        self.reference = QLineEdit(self.values["reference_audio"])
        self.reference_text = QTextEdit()
        self.reference_text.setFixedHeight(75)
        self.reference_text.setPlainText(self.values["reference_text"])
        self.reference_text.setPlaceholderText("Exact words spoken in the reference clip")
        self.reference_language = QComboBox()
        self.reference_language.addItem("English", "en")
        self.reference_language.addItem("Japanese", "ja")
        self.reference_language.setCurrentIndex(self.reference_language.findData(self.values["reference_language"]))
        form.addRow("Voice server", self.tts)
        form.addRow("Reference WAV", self.reference)
        form.addRow(button("Choose reference clip…", self.pick_reference, "quiet"))
        form.addRow("Clip transcript", self.reference_text)
        form.addRow("Clip language", self.reference_language)
        hint = QLabel("Start GPT-SoVITS separately with the appropriate model. The reference clip and model determine the voice. No Raiden voice model or recording is bundled.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        form.addRow(hint)

        form = QFormLayout(guides)
        self.web_mode = QComboBox()
        for label, value in [("Auto · guides and current questions", "auto"),
                             ("Always · search for every question", "always"),
                             ("Off · local knowledge only", "off")]:
            self.web_mode.addItem(label, value)
        self.web_mode.setCurrentIndex(self.web_mode.findData(self.values["web_mode"]))
        form.addRow("Web lookup", self.web_mode)
        self.web_workflow = QComboBox()
        self.web_workflow.addItem("Qwen + SearXNG + Trafilatura", "agent")
        self.web_workflow.addItem("Classic lookup (previous version)", "legacy")
        self.web_workflow.setCurrentIndex(self.web_workflow.findData(self.values["web_workflow"]))
        form.addRow("Guide workflow", self.web_workflow)
        self.searxng = QLineEdit(self.values["searxng_url"])
        form.addRow("SearXNG address", self.searxng)
        self.test_search_button = button("Test search connection", self.test_search, "quiet")
        form.addRow(self.test_search_button)
        self.search_status = QLabel("Run Start-Search.bat once Docker Desktop is running.")
        self.search_status.setWordWrap(True)
        self.search_status.setObjectName("hint")
        form.addRow(self.search_status)
        self.search_jobs = Jobs()
        self.search_jobs.done.connect(lambda _n, _k, value: self.search_checked(value))
        self.search_jobs.failed.connect(lambda _n, _k, value: self.search_checked(value))
        self.web_backend = QComboBox()
        for label, value in [("Auto · available providers", "auto"), ("DuckDuckGo", "duckduckgo"),
                             ("Bing", "bing"), ("Brave", "brave")]:
            self.web_backend.addItem(label, value)
        self.web_backend.setCurrentIndex(self.web_backend.findData(self.values["web_backend"]))
        form.addRow("Classic search engine", self.web_backend)
        self.web_backend.setEnabled(self.web_workflow.currentData() == "legacy")
        self.web_workflow.currentIndexChanged.connect(lambda _: self.web_backend.setEnabled(self.web_workflow.currentData() == "legacy"))
        hint = QLabel("Qwen chooses search phrases, reads pages and retries when details are missing. Answers and numbered source previews appear here in chat. Any game can be researched using its own equipment terms. Pages stay in memory; no guide files are saved.\n\nSearXNG sends search phrases to search engines. Screenshots stay with your local model. New chat clears conversation and sources. No Bionic web-search subscription is used.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        form.addRow(hint)
        self.youtube = QLineEdit(self.values["youtube_key"])
        self.youtube.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("YouTube API key", self.youtube)
        hint = QLabel("Optional: a YouTube API key adds video cards. Videos are not watched or transcribed. Settings, including keys, are saved on this PC.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        form.addRow(hint)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(button("Cancel", self.reject, "quiet"))
        footer.addWidget(button("Save settings", self.commit, "primary"))
        root.addLayout(footer)

    def pick_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose icon", "", "Images (*.png *.webp *.jpg *.jpeg *.bmp)")
        if path:
            self.image.setText(path)

    def test_search(self):
        address = self.searxng.text()
        self.test_search_button.setEnabled(False)
        self.search_status.setText("Checking SearXNG…")
        def work(_status, _cancelled):
            try:
                result = search_searxng(address, "SearXNG documentation")
                return f"Connected · {len(result['results'])} search results. " + result.get("notice", "")
            except ToolError as exc:
                return str(exc)
        self.search_jobs.start("search-test", work)

    def search_checked(self, message):
        self.test_search_button.setEnabled(True)
        self.search_status.setText(message)

    def pick_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose reference voice", "", "WAV audio (*.wav)")
        if path:
            self.reference.setText(path)

    def commit(self):
        values = self.companion.settings.copy()
        values.update({"image_path": self.image.text().strip(), "mascot_size": self.icon_size.value(),
            "opacity": self.opacity.value() / 100, "hotkey": self.hotkey.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
            "base_url": self.base.text().strip(), "model": self.model.text().strip(),
            "local_api_key": self.local_key.text().strip(), "reply_language": self.language.currentData(),
            "qwen_fast_replies": self.fast_replies.isChecked(), "ai_timeout": self.ai_timeout.value(),
            "web_mode": self.web_mode.currentData(), "web_backend": self.web_backend.currentData(),
            "web_workflow": self.web_workflow.currentData(), "searxng_url": self.searxng.text().strip(),
            "voice": self.voice.currentData(), "auto_speak": self.auto.isChecked(),
            "tts_url": self.tts.text().strip(), "reference_audio": self.reference.text().strip(),
            "reference_text": self.reference_text.toPlainText().strip(),
            "reference_language": self.reference_language.currentData(), "youtube_key": self.youtube.text().strip()})
        try:
            local_url(values["base_url"])
            local_url(values["tts_url"])
            values["searxng_url"] = searxng_url(values["searxng_url"])
            if values["image_path"] and QPixmap(values["image_path"]).isNull():
                raise CompanionError("That image could not be opened. Choose a PNG, WebP, JPG, or BMP.")
            self.companion.hotkey.apply(values["hotkey"])
            self.companion.settings = values
            save_settings(values)
            self.companion.pet.apply(values)
            self.companion.panel.refresh_voice_label()
            self.companion.panel.refresh_web_mode()
            self.companion.place_panel()
        except (CompanionError, ToolError, OSError) as exc:
            QMessageBox.warning(self, "Could not save", str(exc))
            return
        self.accept()


class PromptBox(QTextEdit):
    submit = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submit.emit()
        else:
            super().keyPressEvent(event)


class PanelTitle(QLabel):
    """Move the frameless panel using its header, including before enlarging it."""
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.window().pos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and hasattr(self, "drag_offset"):
            point = event.globalPosition().toPoint()
            screen = QApplication.screenAt(point) or self.window().screen()
            bounds = screen.availableGeometry()
            target = point - self.drag_offset
            window = self.window()
            window.move(max(bounds.left(), min(target.x(), bounds.right() - window.width() + 1)),
                        max(bounds.top(), min(target.y(), bounds.bottom() - window.height() + 1)))
            event.accept()
        else:
            super().mouseMoveEvent(event)


class CornerGrip(QSizeGrip):
    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(20, 20)
        self.setToolTip("Drag to resize the chat window. Your size is saved automatically.")
        self.setAccessibleName("Resize chat window")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QColor("#bba0dc"))
        for offset in (5, 9, 13):
            painter.drawLine(18 - offset, 17, 17, 18 - offset)


class Panel(QWidget):
    def __init__(self, companion):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.c = companion
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Raiden Companion")
        self.size_timer = QTimer(self)
        self.size_timer.setSingleShot(True)
        self.size_timer.timeout.connect(self.save_size)
        self.setMinimumSize(438, 560)
        self.resize(*(companion.settings.get("panel_size") or [438, 644]))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        shell = QFrame()
        shell.setObjectName("shell")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 100))
        shell.setGraphicsEffect(shadow)
        layout.addWidget(shell)
        body = QVBoxLayout(shell)
        body.setContentsMargins(22, 18, 22, 18)
        body.setSpacing(12)
        top = QHBoxLayout()
        badge = PanelTitle("RAIDEN COMPANION" + (" · DEMO" if companion.demo else ""))
        badge.setObjectName("eyebrow")
        badge.setCursor(Qt.CursorShape.SizeAllCursor)
        badge.setToolTip("Drag here to move the chat window")
        self.title_handle = badge
        top.addWidget(badge)
        top.addStretch()
        top.addWidget(button("Settings", companion.open_settings, "quiet"))
        top.addWidget(button("×", self.hide, "quiet", "Close panel"))
        body.addLayout(top)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)

        home = QWidget()
        h = QVBoxLayout(home)
        h.setContentsMargins(0, 10, 0, 0)
        portrait = QLabel()
        portrait.setPixmap(QPixmap(str(ICON)).scaled(148, 148, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(portrait)
        title = QLabel("A little guidance?")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(title)
        subtitle = QLabel("A question, a quest, or a moment of clarity.")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(subtitle)
        h.addSpacing(15)
        h.addWidget(button("Ask a question\nChat · Look up game builds and guides", companion.start_chat, "card"))
        h.addWidget(button("Find a game guide\nCapture your screen · Search guides", companion.capture, "card"))
        h.addWidget(button("Use an image file instead", companion.attach_image, "quiet"))
        h.addStretch()
        self.stack.addWidget(home)

        chat = QWidget()
        chat_layout = QVBoxLayout(chat)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        line = QHBoxLayout()
        line.addWidget(button("‹ Home", lambda: self.stack.setCurrentIndex(0), "quiet"))
        line.addStretch()
        line.addWidget(QLabel("Web"))
        self.web_mode = QComboBox()
        for label, value in [("Auto", "auto"), ("Always", "always"), ("Off", "off")]:
            self.web_mode.addItem(label, value)
        self.web_mode.setMinimumWidth(88)
        self.web_mode.setToolTip("Auto searches for guides and current facts. Always forces web lookup. Off uses local knowledge.")
        self.refresh_web_mode()
        self.web_mode.currentIndexChanged.connect(lambda _: companion.set_web_mode(self.web_mode.currentData()))
        line.addWidget(self.web_mode)
        line.addWidget(button("New chat", companion.new_chat, "quiet"))
        chat_layout.addLayout(line)
        self.output = ChatBrowser()
        self.output.setOpenLinks(False)
        self.output.anchorClicked.connect(lambda url: companion.open_link(url))
        self.output.document().setDefaultStyleSheet("body{font-family:'Segoe UI';font-size:13px;color:#eee8fb;} p{line-height:1.45;} a{color:#d2b5ff;} h4{color:#ceaff4;font-size:11px;margin-bottom:5px;}")
        chat_layout.addWidget(self.output, 1)
        image_row = QHBoxLayout()
        self.thumbnail = QLabel()
        self.thumbnail.setFixedSize(90, 55)
        self.image_note = QLabel("Screen attached")
        self.image_note.setObjectName("hint")
        self.remove_image = button("Remove", companion.clear_image, "quiet")
        self.attachment = QWidget()
        image_row.addWidget(self.thumbnail)
        image_row.addWidget(self.image_note, 1)
        image_row.addWidget(self.remove_image)
        self.attachment.setLayout(image_row)
        self.attachment.hide()
        chat_layout.addWidget(self.attachment)
        self.prompt = PromptBox()
        self.prompt.setFixedHeight(80)
        self.prompt.setPlaceholderText("Ask me anything…")
        self.prompt.submit.connect(companion.submit)
        chat_layout.addWidget(self.prompt)
        actions = QHBoxLayout()
        self.capture_button = button("Capture", companion.capture, "quiet")
        self.attach_button = button("Image", companion.attach_image, "quiet")
        actions.addWidget(self.capture_button)
        actions.addWidget(self.attach_button)
        actions.addStretch()
        self.cancel_button = button("Cancel", companion.cancel, "quiet")
        self.cancel_button.hide()
        actions.addWidget(self.cancel_button)
        self.send_button = button("Send", companion.submit, "primary")
        actions.addWidget(self.send_button)
        chat_layout.addLayout(actions)
        self.stack.addWidget(chat)
        self.status = QLabel("Ready when you are")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        body.addWidget(self.status)
        bottom = QHBoxLayout()
        self.voice_label = QLabel()
        self.voice_label.setObjectName("hint")
        bottom.addWidget(self.voice_label, 1)
        bottom.addWidget(button("Copy", lambda: QApplication.clipboard().setText(companion.last_answer),
                                "quiet", "Copy the latest reply, including its original equation notation"))
        bottom.addWidget(button("Read", companion.read_last, "quiet", "Read the latest reply aloud"))
        bottom.addWidget(button("Stop", companion.stop_voice, "quiet", "Stop speech playback"))
        body.addLayout(bottom)
        self.size_grip = CornerGrip(self)
        self.refresh_voice_label()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "size_grip"):
            self.size_grip.move(self.width() - 39, self.height() - 36)
            self.size_grip.raise_()
        self.c.settings["panel_size"] = [self.width(), self.height()]
        self.size_timer.start(400)

    def save_size(self):
        self.c.settings["panel_size"] = [self.width(), self.height()]
        try:
            save_settings(self.c.settings)
        except OSError:
            self.status.setText("Window resized; its size could not be saved to your user folder.")

    def hideEvent(self, event):
        if self.size_timer.isActive():
            self.size_timer.stop()
            self.save_size()
        super().hideEvent(event)

    def reset_size(self):
        self.resize(438, 644)
        self.c.place_panel()
        self.save_size()

    def refresh_voice_label(self):
        names = {"system": "System voice", "gpt_sovits": "Character voice", "off": "Voice off"}
        name = names[self.c.settings["voice"]]
        self.voice_label.setText(name + (" · auto" if self.c.settings["auto_speak"] and self.c.settings["voice"] != "off" else ""))

    def refresh_web_mode(self):
        self.web_mode.blockSignals(True)
        self.web_mode.setCurrentIndex(self.web_mode.findData(self.c.settings["web_mode"]))
        self.web_mode.blockSignals(False)

    def append(self, speaker, text, citations=None):
        self.output.append_reply(speaker, text, rich=(speaker == "Raiden"), citations=citations)

    def busy(self, active):
        self.send_button.setEnabled(not active)
        self.capture_button.setEnabled(not active)
        self.attach_button.setEnabled(not active)
        self.remove_image.setEnabled(not active)
        self.web_mode.setEnabled(not active)
        self.cancel_button.setVisible(active)


class Companion(QObject):
    def __init__(self, demo=False):
        super().__init__()
        self.demo = demo
        self.settings = load_settings()
        self.history, self.image_data, self.last_answer = [], None, ""
        self.guide_context = ""
        self.allowed_links = set()
        self.source_previews = {}
        self.source_dialog = None
        self.job_id, self.voice_id = None, None
        self.capture_pending = False
        self.last_question = ""
        self.dialog = None
        self.audio_file = None
        self.jobs = Jobs()
        self.jobs.done.connect(self.done)
        self.jobs.failed.connect(self.failed)
        self.jobs.progress.connect(self.progress)
        self.pet = Mascot(self.settings)
        self.panel = Panel(self)
        self.pet.clicked.connect(self.toggle)
        self.pet.moved.connect(self.moved)
        self.pet.context_requested.connect(self.context_menu)
        self.position_timer = QTimer()
        self.position_timer.setSingleShot(True)
        self.position_timer.timeout.connect(self.persist_position)
        self.hotkey = NativeHotkey(self.toggle, self.panel)
        # Open audio services only when a reply is actually played.
        self.speech, self.player, self.audio = None, None, None
        self.tray = QSystemTrayIcon(QIcon(str(ICON)), self)
        menu = QMenu()
        menu.addAction("Open / close assistant", self.toggle)
        menu.addAction("Settings", self.open_settings)
        menu.addAction("Bring icon back", self.reset_position)
        menu.addAction("Quit", QApplication.instance().quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Raiden Companion")
        self.tray.activated.connect(lambda reason: self.toggle() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        position = self.settings.get("position")
        if position and QApplication.screenAt(QPoint(*position)):
            self.pet.move(*position)
        else:
            self.reset_position()
        try:
            self.hotkey.apply(self.settings["hotkey"])
        except CompanionError as exc:
            self.panel.status.setText(str(exc))
        self.pet.show()
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        cache_failures = clear_legacy_guide_cache(config_path().parent)
        if cache_failures:
            message = ("Live lookups no longer save guides. These old cache files could not be removed. "
                       "Close any older Raiden instance and restart this version to retry:\n\n" +
                       "\n".join(cache_failures))
            QTimer.singleShot(0, lambda: QMessageBox.warning(self.panel, "Old guide cache", message))

    def reset_position(self):
        rect = QApplication.primaryScreen().availableGeometry()
        self.pet.move(rect.right() - self.pet.width() - 24, rect.bottom() - self.pet.height() - 28)
        self.pet.show()
        self.place_panel()

    def persist_position(self):
        self.settings["position"] = [self.pet.x(), self.pet.y()]
        self.settings["panel_size"] = [self.panel.width(), self.panel.height()]
        try:
            save_settings(self.settings)
        except OSError:
            self.panel.status.setText("Could not save settings to your user folder.")

    def moved(self):
        self.place_panel()
        self.position_timer.start(800)

    def place_panel(self):
        if not hasattr(self, "panel"):
            return
        screen = QApplication.screenAt(self.pet.frameGeometry().center()) or QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        self.panel.setMaximumSize(bounds.size())
        self.panel.resize(min(self.panel.width(), bounds.width()), min(self.panel.height(), bounds.height()))
        x = self.pet.x() - self.panel.width() + 35
        y = self.pet.y() - self.panel.height() + 55
        x = max(bounds.left(), min(x, bounds.right() - self.panel.width()))
        y = max(bounds.top(), min(y, bounds.bottom() - self.panel.height()))
        self.panel.move(x, y)

    def toggle(self):
        if self.capture_pending:
            return
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self.show_panel()

    def show_panel(self):
        self.pet.show()
        self.place_panel()
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def context_menu(self, position):
        menu = QMenu()
        menu.addAction("Ask a question", self.start_chat)
        menu.addAction("Find a game guide", self.capture)
        menu.addAction("Settings", self.open_settings)
        menu.addSeparator()
        menu.addAction("Quit", QApplication.instance().quit)
        menu.exec(position)

    def open_settings(self):
        if self.capture_pending:
            return
        if self.dialog and self.dialog.isVisible():
            self.dialog.raise_()
            return
        self.dialog = SettingsDialog(self)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def start_chat(self):
        self.panel.stack.setCurrentIndex(1)
        self.show_panel()
        self.panel.prompt.setFocus()

    def set_web_mode(self, mode):
        if mode not in ("auto", "always", "off"):
            return
        self.settings["web_mode"] = mode
        try:
            save_settings(self.settings)
        except OSError:
            self.panel.status.setText("Web mode changed for this session; settings could not be saved.")

    def new_chat(self):
        self.cancel()
        self.stop_voice()
        self.history = []
        self.guide_context = ""
        self.last_answer = ""
        self.allowed_links.clear()
        self.source_previews.clear()
        if self.source_dialog:
            self.source_dialog.close()
        self.panel.output.clear()
        self.panel.prompt.clear()
        self.clear_image()
        self.panel.status.setText("A fresh conversation")

    def clear_image(self):
        self.image_data = None
        self.panel.attachment.hide()
        self.panel.prompt.setPlaceholderText("Ask me anything…")
        self.panel.send_button.setText("Send")

    def attach(self, pixmap):
        if pixmap.isNull():
            self.panel.status.setText("Capture was empty. Use borderless windowed mode or choose an image file.")
            return
        # Keep small quest text readable without sending a full 4K image.
        scaled = pixmap.scaled(1920, 1920, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not scaled.save(buffer, "JPG", 90):
            self.panel.status.setText("The image could not be prepared.")
            return
        self.image_data = bytes(data)
        self.panel.thumbnail.setPixmap(pixmap.scaled(90, 55, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.panel.attachment.show()
        self.panel.prompt.setPlaceholderText("Optional: game, quest, or what you're stuck on…")
        self.panel.send_button.setText("Find guide")
        self.start_chat()
        self.panel.status.setText("Image ready · Add context or press Find guide")

    def attach_image(self):
        if self.job_id is not None or self.capture_pending:
            return
        path, _ = QFileDialog.getOpenFileName(self.panel, "Choose game screenshot", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self.attach(QPixmap(path))

    def capture(self):
        if self.job_id is not None or self.capture_pending:
            return
        if self.demo:
            self.start_chat()
            self.panel.append("Preview", "In normal mode, this hides the assistant and captures the monitor where its icon is placed. No screen is captured in demo mode.")
            return
        self.stop_voice()
        self.capture_pending = True
        screen = QApplication.screenAt(self.pet.frameGeometry().center()) or QApplication.primaryScreen()
        self.panel.hide()
        self.pet.hide()
        if self.source_dialog:
            self.source_dialog.hide()
        if self.dialog:
            self.dialog.hide()

        def take():
            try:
                pixmap = screen.grabWindow(0)
                self.attach(pixmap)
            finally:
                self.capture_pending = False
                self.show_panel()
        QTimer.singleShot(450, take)

    def submit(self):
        if self.job_id is not None or self.capture_pending:
            return
        text = self.panel.prompt.toPlainText().strip()
        if not text and self.image_data is None:
            return
        if self.demo:
            self.panel.append("You", text or "Find a guide for this screen")
            self.panel.append("Preview", "This is a UI preview. Start the app normally and connect your local model in Settings to receive real answers.")
            self.panel.prompt.clear()
            return
        self.stop_voice()
        self.last_question = text or "Find a guide for this screen."
        self.panel.append("You", self.last_question + (" [image attached]" if self.image_data else ""))
        self.panel.prompt.clear()
        self.panel.busy(True)
        settings, history, image_data = self.settings.copy(), self.history.copy(), self.image_data
        guide_context = self.guide_context
        kind = "guide" if image_data else "chat"

        def work(status, cancelled):
            ai = LocalAI(settings, status, cancelled)
            return ai.guide(image_data, text) if image_data else ai.answer(text, history, guide_context)
        self.job_id = self.jobs.start(kind, work)
        self.panel.status.setText("Reading the game screen…" if image_data else "Thinking about your question…")

    def cancel(self):
        if self.job_id is not None:
            self.jobs.cancel(self.job_id)
        self.job_id = None
        self.panel.busy(False)
        self.panel.status.setText("Ready when you are")

    def progress(self, number, text):
        if number in (self.job_id, self.voice_id):
            self.panel.status.setText(text)

    def done(self, number, kind, result):
        if kind == "voice":
            if number != self.voice_id:
                return
            self.voice_id = None
            if self.player is None:
                self.player = QMediaPlayer(self)
                self.audio = QAudioOutput(self)
                self.player.setAudioOutput(self.audio)
                self.player.errorOccurred.connect(lambda *_: self.panel.status.setText("Audio playback failed. Check your output device."))
            fd, name = tempfile.mkstemp(prefix="raiden-voice-", suffix=".wav")
            with os.fdopen(fd, "wb") as f:
                f.write(result)
            self.audio_file = name
            self.player.setSource(QUrl.fromLocalFile(name))
            self.player.play()
            self.panel.status.setText("Speaking…")
            return
        if number != self.job_id:
            return
        self.job_id = None
        self.panel.busy(False)
        self.last_answer = result["answer"]
        query = result.get("search_query", "")
        if result.get("guide_context") or BUILD_WORDS.search(query):
            self.guide_context = result.get("guide_context") or query
        sources = result.get("sources", [])
        citations, chips = {}, []
        token = uuid.uuid4().hex
        for source in sources:
            if not public_url(source.get("url")):
                continue
            link = f"raiden-source:/{token}/{source['id']}"
            self.source_previews[link] = source
            citations[str(source["id"])] = link
            self.allowed_links.add(QUrl(source["url"]).toString())
            host = QUrl(source["url"]).host().removeprefix("www.")
            chips.append(f'<a href="{link}" style="color:#d5b8ff;background-color:#30253f;">'
                         f'&nbsp;[{source["id"]}] {html.escape(host)}&nbsp;</a>')
        self.panel.append("Raiden", result["answer"], citations)
        if kind == "chat":
            # Keep the resolved subject after an in-chat game clarification,
            # so the next "her weapon?" still refers to the same character.
            context_question = result.get("search_query") or self.last_question
            # A failed lookup still changes the requested subject. Keep the
            # question for a correction/retry, without treating fallback prose
            # as model evidence or silently reverting to the last success.
            self.history.append({"role": "user", "content": context_question})
            if result.get("remember", True):
                self.history.append({"role": "assistant", "content": result["answer"]})
            self.history = self.history[-10:]
        if chips:
            self.panel.output.append('<h4 style="color:#ceaff4;">SOURCES · CLICK TO READ HERE</h4><p>' +
                                     " &nbsp; ".join(chips) + '</p>')
        for video in result["videos"]:
            self.add_result_link(video["url"], video["title"], video["channel"])
        if result["query"] and not sources:
            url = youtube_url(result["query"])
            self.add_result_link(url, "Open YouTube search ↗", result["query"])
        if result.get("evidence_label"):
            self.panel.output.append('<p style="color:#b4a8c6;font-size:11px;">' +
                                     html.escape(result["evidence_label"]) + '</p>')
        elif result["notice"]:
            self.panel.append("Lookup details", result["notice"])
        if not result.get("remember", True) and not self.panel.prompt.toPlainText().strip():
            self.panel.prompt.setPlainText(self.last_question)
        self.panel.status.setText("Ready for your next question")
        self.panel.status.setToolTip(result.get("notice", ""))
        if self.settings["auto_speak"]:
            self.read_last()

    def failed(self, number, kind, message):
        if kind == "voice":
            if number == self.voice_id:
                self.voice_id = None
                self.panel.status.setText(message)
            return
        if number == self.job_id:
            self.job_id = None
            self.panel.busy(False)
            self.panel.append("Request error", message)
            self.panel.prompt.setPlainText(self.last_question if self.last_question != "Find a guide for this screen." else "")
            self.panel.status.setText("See the error above; your question is ready to retry")

    def add_result_link(self, url, title, detail=""):
        safe = public_url(url)
        if not safe:
            return
        safe = QUrl(safe).toString()
        self.allowed_links.add(safe)
        self.panel.output.append(
            f'<p><a href="{html.escape(safe, quote=True)}">{html.escape(title)}</a>'
            f'<br><small>{html.escape(detail)}</small></p>')

    def open_link(self, url):
        source = self.source_previews.get(url.toString())
        if source:
            self.show_source(source)
            return
        # Only links registered from retrieved results or app-built searches.
        if url.toString() in self.allowed_links and public_url(url.toString()):
            QDesktopServices.openUrl(url)

    def show_source(self, source):
        if self.source_dialog:
            self.source_dialog.close()
        dialog = QDialog(self.panel, Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.source_dialog = dialog
        dialog.setWindowTitle("Guide source · " + source["title"])
        bounds = self.panel.screen().availableGeometry()
        dialog.resize(min(650, bounds.width()), min(680, bounds.height()))
        dialog.setSizeGripEnabled(True)
        layout = QVBoxLayout(dialog)
        label = QLabel(source["title"])
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        info = QLabel(source["coverage"] + " · " + source.get("fetched_at", "Date unavailable"))
        info.setTextFormat(Qt.TextFormat.PlainText)
        info.setWordWrap(True)
        info.setObjectName("hint")
        layout.addWidget(info)
        browser = ChatBrowser()
        browser.setOpenLinks(False)
        browser.append_reply("Source excerpt", source.get("excerpt") or source.get("snippet") or "No page text available.", rich=False)
        layout.addWidget(browser, 1)
        actions = QHBoxLayout()
        actions.addWidget(button("Original page (browser)", lambda: self.open_link(QUrl(source["url"])), "quiet"))
        actions.addStretch()
        actions.addWidget(button("Back to chat", dialog.close, "primary"))
        layout.addLayout(actions)
        dialog.show()
        x = max(bounds.left(), min(self.panel.x(), bounds.right() - dialog.width() + 1))
        y = max(bounds.top(), min(self.panel.y(), bounds.bottom() - dialog.height() + 1))
        dialog.move(x, y)
        dialog.raise_()

    def read_last(self):
        self.stop_voice()
        if not self.last_answer or self.settings["voice"] == "off" or self.demo:
            return
        text = speech_text(self.last_answer)
        if self.settings["voice"] == "system":
            if self.speech is None:
                self.speech = QTextToSpeech(self)
                self.speech.errorOccurred.connect(lambda *_: self.panel.status.setText("System speech is unavailable. Configure a voice in Settings."))
            if self.speech.state() == QTextToSpeech.State.Error:
                self.panel.status.setText("No system voice available. Choose a speech engine in Settings.")
                return
            self.speech.say(text)
            self.panel.status.setText("Reading with the system voice…")
        else:
            settings = self.settings.copy()
            self.panel.status.setText("Preparing character speech…")
            self.voice_id = self.jobs.start("voice", lambda status, cancelled: synthesize(settings, text))

    def stop_voice(self):
        if self.voice_id is not None:
            self.jobs.cancel(self.voice_id)
        self.voice_id = None
        if self.speech is not None:
            self.speech.stop()
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
        if self.audio_file:
            try:
                Path(self.audio_file).unlink(missing_ok=True)
            except OSError:
                pass
            self.audio_file = None

    def shutdown(self):
        self.cancel()
        self.stop_voice()
        self.hotkey.close()
        self.persist_position()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="Open the UI with no model calls, screenshots, or speech")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Raiden Companion")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(STYLE)
    app.setWindowIcon(QIcon(str(ICON)))
    def report_exception(exc_type, exc, tb):
        import traceback
        message = "".join(traceback.format_exception(exc_type, exc, tb))
        path = config_path().parent / "error.log"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(message, encoding="utf-8")
        except OSError:
            pass
        QMessageBox.critical(None, "Raiden Companion", "The app encountered an error. Details are in:\n" + str(path))
    sys.excepthook = report_exception
    companion = Companion(args.demo)
    companion.show_panel()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
