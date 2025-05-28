from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QSpinBox, QDoubleSpinBox,
    QPushButton, QHBoxLayout
)
import json
import os

class VTuberConfigWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VTuber Position & Scale Config")
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        self.position_controls = {}
        self.scale_controls = {}

        # Position controls
        for label, key in [
            ("Left Eye", "left_eye"),
            ("Right Eye", "right_eye"),
            ("Mouth", "mouth")
        ]:
            h = QHBoxLayout()
            x_spin = QSpinBox()
            x_spin.setMaximum(2000)
            y_spin = QSpinBox()
            y_spin.setMaximum(2000)
            self.position_controls[key] = (x_spin, y_spin)
            h.addWidget(QLabel(label))
            h.addWidget(QLabel("X:")); h.addWidget(x_spin)
            h.addWidget(QLabel("Y:")); h.addWidget(y_spin)
            self.layout.addLayout(h)

        # Scale controls
        for label, key, default in [
            ("Eye Scale", "eye_scale", 1.0),
            ("Mouth Scale", "mouth_scale", 1.0),
            ("Image Scale", "image_scale", 0.5),
        ]:
            h = QHBoxLayout()
            spin = QDoubleSpinBox()
            spin.setRange(0.01, 5.0)
            spin.setSingleStep(0.01)
            self.scale_controls[key] = spin
            h.addWidget(QLabel(label))
            h.addWidget(spin)
            self.layout.addLayout(h)

        # Save button
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save_settings)
        self.layout.addWidget(self.save_button)

        self.load_settings()

    def load_settings(self):
        if os.path.exists("settings.json"):
            with open("settings.json", "r", encoding="utf-8") as f:
                data = json.load(f).get("vtuber", {})
        else:
            data = {}

        pos_defaults = {
            "left_eye": (200, 245),
            "right_eye": (265, 245),
            "mouth": (245, 306)
        }

        scale_defaults = {
            "eye_scale": 1.0,
            "mouth_scale": 1.0,
            "image_scale": 0.5
        }

        # Load position values
        for key, (x_spin, y_spin) in self.position_controls.items():
            x, y = data.get(key, pos_defaults[key])
            x_spin.setValue(x)
            y_spin.setValue(y)

        # Load scale values
        for key, spin in self.scale_controls.items():
            value = data.get(key, scale_defaults[key])
            spin.setValue(value)

    def save_settings(self):
        vtuber_data = {
            key: (x.value(), y.value())
            for key, (x, y) in self.position_controls.items()
        }

        for key, spin in self.scale_controls.items():
            vtuber_data[key] = spin.value()

        if os.path.exists("settings.json"):
            with open("settings.json", "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except Exception:
                    data = {}
        else:
            data = {}

        data["vtuber"] = vtuber_data

        with open("settings.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        self.close()
