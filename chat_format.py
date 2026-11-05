"""Offline Markdown and equation display for the existing Qt chat panel."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import html
import io
import re
import uuid

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QColor, QFont, QImage, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextLength, QTextTable
from PySide6.QtWidgets import QTextBrowser

from core import math_spans


@lru_cache(maxsize=256)
def equation_png(formula: str, display: bool) -> bytes:
    """Render Mathtext without a TeX install, subprocess, or network request."""
    if len(formula) > 2048:
        raise ValueError("Equation is too long")
    # These TeX presentation switches are unnecessary in Mathtext.
    formula = re.sub(r"\\(?:displaystyle|textstyle)\b", "", formula)
    formula = formula.replace(r"\tfrac", r"\frac")
    if display:
        formula = re.sub(r"\\frac\b", r"\\dfrac", formula)
    from matplotlib import rc_context
    from matplotlib.font_manager import FontProperties
    from matplotlib.mathtext import math_to_image
    result = io.BytesIO()
    with rc_context({"text.usetex": False, "mathtext.fontset": "dejavusans",
                     "savefig.transparent": True}):
        math_to_image(
            "$" + formula + "$", result, format="png", dpi=192,
            prop=FontProperties(size=12 if display else 11),
            color="#f1eafb",
        )
    return result.getvalue()


class _OfflineDocument(QTextDocument):
    def loadResource(self, resource_type, name):
        # Markdown must not load remote images or arbitrary local files.
        return QImage()


class ChatBrowser(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.equation_sizes = {}
        self.reflow_timer = QTimer(self)
        self.reflow_timer.setSingleShot(True)
        self.reflow_timer.timeout.connect(self._resize_equations)

    def clear(self):
        self.equation_sizes.clear()
        super().clear()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "reflow_timer"):
            self.reflow_timer.start(0)

    def _resize_equations(self):
        available = max(120, self.viewport().width() - 32)
        changes = []
        block = self.document().begin()
        while block.isValid():
            fragment = block.begin()
            while not fragment.atEnd():
                item = fragment.fragment()
                fmt = item.charFormat()
                if fmt.isImageFormat():
                    image = fmt.toImageFormat()
                    natural = self.equation_sizes.get(image.name())
                    if natural:
                        width, height = natural
                        scale = min(1.0, available / max(width, 1))
                        if abs(image.width() - width * scale) > 0.2:
                            image.setWidth(width * scale)
                            image.setHeight(height * scale)
                            changes.append((item.position(), item.length(), image))
                fragment += 1
            block = block.next()
        if changes:
            cursor = QTextCursor(self.document())
            cursor.beginEditBlock()
            for position, length, fmt in changes:
                cursor.setPosition(position)
                cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
                cursor.setCharFormat(fmt)
            cursor.endEditBlock()

    def append(self, text: str):
        # QTextEdit.append inherits the previous list/character format. Start
        # each message or source card outside a model's ending bullet list.
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.document().isEmpty():
            cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
        cursor.setBlockFormat(QTextBlockFormat())
        cursor.setCharFormat(QTextCharFormat())
        cursor.insertHtml(text)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def loadResource(self, resource_type, name):
        # Equations are registered explicitly with addResource. Unknown
        # resources, including remote and file: images, remain unavailable.
        return QImage()

    def append_reply(self, speaker: str, text: str, rich: bool = True, citations: dict | None = None):
        title = '<h4 style="color:#ceaff4;font-size:11px;">' + html.escape(speaker.upper()) + "</h4>"
        if not rich:
            body = "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"
        else:
            body = self._reply_html(text)
        if citations:
            # Only app-registered source tokens become links. Model-generated
            # links remain stripped, including links hidden in Markdown/HTML.
            parts = re.split(r"(<[^>]+>)", body)
            for i in range(0, len(parts), 2):
                parts[i] = re.sub(r"\[(\d+)\]", lambda m:
                    '<a href="' + html.escape(citations[m[1]], quote=True) +
                    '" style="color:#d5b8ff;background-color:#30253f;">[' + m[1] + ']</a>'
                    if m[1] in citations else m[0], parts[i])
            body = "".join(parts)
        self.append(title + body)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def _reply_html(self, text: str) -> str:
        replacements = {}
        spans = list(math_spans(text))[:64]
        prefix = "RAIDENMATH" + uuid.uuid4().hex
        # Replace math before Markdown consumes backslashes or underscores.
        for index, (start, end, formula, display) in reversed(list(enumerate(spans))):
            # Tall notation needs its own line in the compact overlay, even
            # when the model supplied inline dollar delimiters.
            display = display or bool(re.search(r"\\(?:[dt]?frac|sum|prod|int|lim)\b", formula))
            marker = prefix + str(index) + "END"
            replacement = self._equation_html(formula, display)
            # Keep sentence punctuation with a promoted equation instead of
            # leaving a stray period at the beginning of the next paragraph.
            punctuation = text[end:end + 1]
            if display and punctuation and punctuation in ".,;:!?":
                replacement += html.escape(punctuation)
                end += 1
            replacements[marker] = replacement
            if display:
                marker = "\n\n" + marker + "\n\n"
            text = text[:start] + marker + text[end:]

        document = _OfflineDocument()
        font = QFont("Segoe UI")
        font.setPixelSize(13)
        document.setDefaultFont(font)
        features = (QTextDocument.MarkdownFeature.MarkdownDialectGitHub
                    | QTextDocument.MarkdownFeature.MarkdownNoHTML)
        document.setMarkdown(text, features)
        # Style Qt table objects before serialization. Replacing every td/th
        # tag as a string loses spans and other structure needed for copying.
        def style_tables(frame):
            for child in frame.childFrames():
                if isinstance(child, QTextTable):
                    fmt = child.format()
                    fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
                    fmt.setBorder(0)
                    fmt.setCellSpacing(0)
                    fmt.setCellPadding(6)
                    child.setFormat(fmt)
                    for row in range(child.rows()):
                        for col in range(child.columns()):
                            cell = child.cellAt(row, col)
                            style = cell.format().toTableCellFormat()
                            style.setBottomBorder(1)
                            style.setBottomBorderBrush(QColor("#493853"))
                            cell.setFormat(style)
                style_tables(child)
        style_tables(document.rootFrame())
        serialized = document.toHtml()
        body_match = re.search(r"<body\b[^>]*>(.*)</body>", serialized, flags=re.S | re.I)
        body = body_match.group(1) if body_match else html.escape(text)
        # No user/model-supplied image, link, or stylesheet is loaded. The app
        # inserts retrieved web and YouTube links separately after this method.
        body = re.sub(r"<img\b[^>]*>", "", body, flags=re.I)
        body = re.sub(r"</?a\b[^>]*>", "", body, flags=re.I)
        for marker, equation in replacements.items():
            body = body.replace(marker, equation)
        return body

    def _equation_html(self, formula: str, display: bool) -> str:
        try:
            image = QImage.fromData(equation_png(formula, display), "PNG")
            if image.isNull():
                raise ValueError("Invalid equation image")
            available = max(120, self.viewport().width() - 32)
            width, height = image.width() / 2, image.height() / 2
            scale = min(1.0, available / max(width, 1))
            digest = hashlib.sha256((str(display) + formula).encode()).hexdigest()
            url = QUrl("raiden-math:/" + digest)
            self.equation_sizes[url.toString()] = (width, height)
            self.document().addResource(QTextDocument.ResourceType.ImageResource, url, image)
            return (f'<img src="{url.toString()}" width="{width * scale:.1f}" '
                    f'height="{height * scale:.1f}" style="vertical-align:middle;" '
                    f'alt="{html.escape(formula, quote=True)}" '
                    f'title="{html.escape(formula, quote=True)}">')
        except (ImportError, ValueError, RuntimeError, OverflowError, RecursionError):
            # Unsupported LaTeX remains readable/copyable and never breaks a reply.
            return "<code>" + html.escape(formula) + "</code>"
