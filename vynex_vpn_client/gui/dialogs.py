from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import design_tokens as tokens


_BUTTON_TEXTS: dict[QMessageBox.StandardButton, str] = {
    QMessageBox.StandardButton.Ok: "ОК",
    QMessageBox.StandardButton.Yes: "Да",
    QMessageBox.StandardButton.No: "Нет",
    QMessageBox.StandardButton.Cancel: "Отмена",
    QMessageBox.StandardButton.Close: "Закрыть",
}


def _apply_message_button_texts(
    message_box: QMessageBox,
    overrides: dict[QMessageBox.StandardButton, str] | None = None,
) -> None:
    labels = {**_BUTTON_TEXTS, **(overrides or {})}
    for button, text in labels.items():
        widget = message_box.button(button)
        if widget is not None:
            widget.setText(text)


def _show_message_box(
    parent: QWidget | None,
    title: str,
    message: str,
    *,
    icon: QMessageBox.Icon,
    buttons: QMessageBox.StandardButton,
    default_button: QMessageBox.StandardButton,
    escape_button: QMessageBox.StandardButton | None = None,
    button_texts: dict[QMessageBox.StandardButton, str] | None = None,
) -> QMessageBox.StandardButton:
    message_box = QMessageBox(parent)
    message_box.setWindowTitle(title)
    message_box.setText(message)
    message_box.setIcon(icon)
    message_box.setMinimumWidth(tokens.SPACE_12 * 10)
    message_box.setStandardButtons(buttons)
    message_box.setDefaultButton(default_button)
    if escape_button is not None:
        message_box.setEscapeButton(escape_button)
    _apply_message_button_texts(message_box, button_texts)
    return QMessageBox.StandardButton(message_box.exec())


def show_error_dialog(parent: QWidget | None, title: str, message: str) -> None:
    _show_message_box(
        parent,
        title,
        message,
        icon=QMessageBox.Icon.Critical,
        buttons=QMessageBox.StandardButton.Ok,
        default_button=QMessageBox.StandardButton.Ok,
        escape_button=QMessageBox.StandardButton.Ok,
    )


def show_info_dialog(parent: QWidget | None, title: str, message: str) -> None:
    _show_message_box(
        parent,
        title,
        message,
        icon=QMessageBox.Icon.Information,
        buttons=QMessageBox.StandardButton.Ok,
        default_button=QMessageBox.StandardButton.Ok,
        escape_button=QMessageBox.StandardButton.Ok,
    )


def show_warning_dialog(parent: QWidget | None, title: str, message: str) -> None:
    _show_message_box(
        parent,
        title,
        message,
        icon=QMessageBox.Icon.Warning,
        buttons=QMessageBox.StandardButton.Ok,
        default_button=QMessageBox.StandardButton.Ok,
        escape_button=QMessageBox.StandardButton.Ok,
    )


def ask_question(
    parent: QWidget | None,
    title: str,
    message: str,
    *,
    buttons: QMessageBox.StandardButton,
    default_button: QMessageBox.StandardButton,
    escape_button: QMessageBox.StandardButton | None = None,
    button_texts: dict[QMessageBox.StandardButton, str] | None = None,
) -> QMessageBox.StandardButton:
    return _show_message_box(
        parent,
        title,
        message,
        icon=QMessageBox.Icon.Question,
        buttons=buttons,
        default_button=default_button,
        escape_button=escape_button,
        button_texts=button_texts,
    )


def _localize_dialog_buttons(buttons: QDialogButtonBox) -> None:
    labels = {
        QDialogButtonBox.StandardButton.Ok: "ОК",
        QDialogButtonBox.StandardButton.Cancel: "Отмена",
        QDialogButtonBox.StandardButton.Close: "Закрыть",
    }
    for button, text in labels.items():
        widget = buttons.button(button)
        if widget is not None:
            widget.setText(text)


def ask_text(
    parent: QWidget | None,
    title: str,
    label: str,
    *,
    default: str = "",
) -> tuple[str, bool]:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(tokens.SPACE_5 * 26, tokens.SPACE_5 * 8)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(*tokens.spacing(tokens.SPACE_5, tokens.SPACE_5, tokens.SPACE_5, tokens.SPACE_4))
    layout.setSpacing(tokens.SPACE_3)
    prompt = QLabel(label)
    prompt.setObjectName("PageSubtitle")
    prompt.setWordWrap(True)
    editor = QLineEdit()
    editor.setText(default)
    editor.selectAll()
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    _localize_dialog_buttons(buttons)

    layout.addWidget(prompt)
    layout.addWidget(editor)
    layout.addWidget(buttons)

    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return "", False
    return editor.text(), True


def ask_multiline_text(
    parent: QWidget | None,
    title: str,
    label: str,
    *,
    default: str = "",
) -> str | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(tokens.SPACE_4 * 40, tokens.SPACE_5 * 18)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(*tokens.spacing(tokens.SPACE_5, tokens.SPACE_5, tokens.SPACE_5, tokens.SPACE_4))
    layout.setSpacing(tokens.SPACE_3)
    prompt = QLabel(label)
    prompt.setObjectName("PageSubtitle")
    prompt.setWordWrap(True)
    editor = QTextEdit()
    editor.setPlainText(default)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    _localize_dialog_buttons(buttons)

    layout.addWidget(prompt)
    layout.addWidget(editor, 1)
    layout.addWidget(buttons)

    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return editor.toPlainText()


def ask_confirmation(
    parent: QWidget | None,
    title: str,
    message: str,
    *,
    default_yes: bool = False,
    yes_text: str = "Да",
    no_text: str = "Нет",
) -> bool:
    default_button = QMessageBox.StandardButton.Yes if default_yes else QMessageBox.StandardButton.No
    result = ask_question(
        parent,
        title,
        message,
        buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        default_button=default_button,
        escape_button=QMessageBox.StandardButton.No,
        button_texts={
            QMessageBox.StandardButton.Yes: yes_text,
            QMessageBox.StandardButton.No: no_text,
        },
    )
    return result == QMessageBox.StandardButton.Yes
