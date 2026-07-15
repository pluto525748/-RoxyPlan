import json
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PET_CONFIG_FILE = PROJECT_ROOT / "data" / "pet_config.json"
KNOWLEDGE_DIR = PROJECT_ROOT / "data" / "knowledge"

DEFAULT_SETTINGS = {
    "pet_scale": 1.0,
    "pet_always_on_top": True,
    "auto_tips_enabled": True,
    "auto_tips_min_minutes": 5,
    "auto_tips_max_minutes": 15,
    "test_mode": False,
    "study_reminder_seconds_test": 10,
    "sleep_seconds_test": 30,
    "study_reminder_minutes": 25,
    "sleep_minutes": 20,
    "pet_asset_path": "assets/pet/roxy_pet_transparent.png",
    "model_name": "qwen3:4b",
}


def load_pet_settings(config_file=PET_CONFIG_FILE):
    config = dict(DEFAULT_SETTINGS)
    try:
        with Path(config_file).open("r", encoding="utf-8") as file:
            loaded = json.load(file)
        if isinstance(loaded, dict):
            config.update(loaded)
    except (OSError, json.JSONDecodeError) as error:
        print(f"[SETTINGS] failed to load {config_file}: {error}")
    return config


def save_pet_settings(config, config_file=PET_CONFIG_FILE):
    path = Path(config_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
        file.write("\n")


class SettingsDialog(QDialog):
    settings_saved = Signal(dict)

    def __init__(self, parent=None, config_file=PET_CONFIG_FILE):
        super().__init__(parent)
        self.config_file = Path(config_file)
        self.config = load_pet_settings(self.config_file)

        self.setWindowTitle("RoxyPlan 设置")
        self.setMinimumWidth(390)
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        title = QLabel("桌宠设置")
        title.setStyleSheet("font: 600 18px 'Microsoft YaHei'; color: #333333;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)

        self.test_mode = QCheckBox("使用秒级测试间隔")
        form.addRow("测试模式", self.test_mode)

        self.always_on_top = QCheckBox("桌宠保持在窗口上方")
        form.addRow("始终置顶", self.always_on_top)

        self.auto_tips_enabled = QCheckBox("允许桌宠自动弹出提示")
        form.addRow("自动提示", self.auto_tips_enabled)

        self.auto_tips_min_minutes = self._spin_box(1, 1440, " 分钟")
        form.addRow("提示最小间隔", self.auto_tips_min_minutes)

        self.auto_tips_max_minutes = self._spin_box(1, 1440, " 分钟")
        form.addRow("提示最大间隔", self.auto_tips_max_minutes)

        self.pet_scale = QDoubleSpinBox()
        self.pet_scale.setRange(0.65, 1.8)
        self.pet_scale.setSingleStep(0.05)
        self.pet_scale.setDecimals(2)
        self.pet_scale.setSuffix(" 倍")
        form.addRow("桌宠缩放", self.pet_scale)

        self.study_test_seconds = self._spin_box(1, 3600, " 秒")
        form.addRow("测试学习提醒", self.study_test_seconds)

        self.sleep_test_seconds = self._spin_box(1, 3600, " 秒")
        form.addRow("测试睡眠时间", self.sleep_test_seconds)

        self.study_minutes = self._spin_box(1, 1440, " 分钟")
        form.addRow("学习提醒时间", self.study_minutes)

        self.sleep_minutes = self._spin_box(1, 1440, " 分钟")
        form.addRow("睡眠时间", self.sleep_minutes)

        asset_row = QWidget()
        asset_layout = QHBoxLayout(asset_row)
        asset_layout.setContentsMargins(0, 0, 0, 0)
        asset_layout.setSpacing(8)
        self.asset_path = QLineEdit()
        browse_button = QPushButton("浏览")
        browse_button.clicked.connect(self._browse_asset)
        asset_layout.addWidget(self.asset_path, 1)
        asset_layout.addWidget(browse_button)
        form.addRow("桌宠图片", asset_row)

        self.model_name = QLineEdit()
        self.model_name.setPlaceholderText("例如 qwen3:4b")
        form.addRow("模型名称", self.model_name)

        open_knowledge_button = QPushButton("打开知识库文件夹")
        open_knowledge_button.clicked.connect(self._open_knowledge_folder)
        form.addRow("本地知识库", open_knowledge_button)
        layout.addLayout(form)

        note = QLabel("计时、提示、缩放和置顶设置会尽量立即生效；桌宠图片和模型名称建议重启 RoxyPlan 后生效。")
        note.setWordWrap(True)
        note.setStyleSheet("font: 12px 'Microsoft YaHei'; color: #777777;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setStyleSheet(
            "QDialog { background: #F7F4FF; }"
            "QLabel, QCheckBox { font: 13px 'Microsoft YaHei'; color: #333333; }"
            "QLineEdit, QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #D8CCFF; "
            "border-radius: 6px; padding: 6px; }"
            "QPushButton { background: #8EA7FF; color: white; border: none; "
            "border-radius: 6px; padding: 7px 12px; }"
            "QPushButton:hover { background: #7894F8; }"
        )

    @staticmethod
    def _spin_box(minimum, maximum, suffix):
        spin_box = QSpinBox()
        spin_box.setRange(minimum, maximum)
        spin_box.setSuffix(suffix)
        return spin_box

    def _load_values(self):
        self.test_mode.setChecked(bool(self.config.get("test_mode", False)))
        self.always_on_top.setChecked(bool(self.config.get("pet_always_on_top", True)))
        self.auto_tips_enabled.setChecked(bool(self.config.get("auto_tips_enabled", True)))
        self.auto_tips_min_minutes.setValue(
            int(self.config.get("auto_tips_min_minutes", 5))
        )
        self.auto_tips_max_minutes.setValue(
            int(self.config.get("auto_tips_max_minutes", 15))
        )
        self.pet_scale.setValue(float(self.config.get("pet_scale", 1.0)))
        self.study_test_seconds.setValue(
            int(self.config.get("study_reminder_seconds_test", 10))
        )
        self.sleep_test_seconds.setValue(
            int(self.config.get("sleep_seconds_test", 30))
        )
        self.study_minutes.setValue(int(self.config.get("study_reminder_minutes", 25)))
        self.sleep_minutes.setValue(int(self.config.get("sleep_minutes", 20)))
        self.asset_path.setText(str(self.config.get("pet_asset_path", "")))
        self.model_name.setText(str(self.config.get("model_name", "qwen3:4b")))

    def _browse_asset(self):
        initial_path = self.asset_path.text().strip()
        if initial_path and not Path(initial_path).is_absolute():
            initial_path = str(PROJECT_ROOT / initial_path)
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择桌宠图片",
            initial_path or str(PROJECT_ROOT / "assets" / "pet"),
            "图片文件 (*.png *.jpg *.jpeg *.webp *.svg)",
        )
        if selected:
            selected_path = Path(selected)
            try:
                selected_path = selected_path.relative_to(PROJECT_ROOT)
            except ValueError:
                pass
            self.asset_path.setText(selected_path.as_posix())

    def _open_knowledge_folder(self):
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(KNOWLEDGE_DIR)))

    def _save(self):
        asset_path = self.asset_path.text().strip()
        model_name = self.model_name.text().strip()
        if not asset_path or not model_name:
            QMessageBox.warning(self, "无法保存", "桌宠图片路径和模型名称不能为空。")
            return
        if self.auto_tips_max_minutes.value() < self.auto_tips_min_minutes.value():
            QMessageBox.warning(self, "无法保存", "提示最大间隔不能小于最小间隔。")
            return

        self.config.update(
            {
                "pet_scale": round(self.pet_scale.value(), 2),
                "pet_always_on_top": self.always_on_top.isChecked(),
                "auto_tips_enabled": self.auto_tips_enabled.isChecked(),
                "auto_tips_min_minutes": self.auto_tips_min_minutes.value(),
                "auto_tips_max_minutes": self.auto_tips_max_minutes.value(),
                "test_mode": self.test_mode.isChecked(),
                "study_reminder_seconds_test": self.study_test_seconds.value(),
                "sleep_seconds_test": self.sleep_test_seconds.value(),
                "study_reminder_minutes": self.study_minutes.value(),
                "sleep_minutes": self.sleep_minutes.value(),
                "pet_asset_path": asset_path,
                "model_name": model_name,
            }
        )
        try:
            save_pet_settings(self.config, self.config_file)
        except OSError as error:
            QMessageBox.critical(self, "保存失败", str(error))
            return

        print(f"[SETTINGS] saved {self.config_file}")
        self.settings_saved.emit(dict(self.config))
        QMessageBox.information(
            self,
            "设置已保存",
            "测试模式、学习提醒、睡眠计时、自动提示、缩放和置顶设置已尽量立即生效。\n"
            "桌宠图片路径、模型名称建议重启 RoxyPlan 后生效。",
        )
        self.accept()
