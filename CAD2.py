import sys
import requests #type: ignore
import json
import tempfile
import os
import cadquery as cq #type: ignore
import pyvista as pv #type: ignore
from pyvistaqt import QtInteractor #type: ignore
import base64 #type: ignore

from PyQt5.QtWidgets import ( #type: ignore
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QTextEdit, QPushButton, QLabel, QSizePolicy, QMessageBox,
    QFileDialog, QAction, QToolBar
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize #type: ignore
from PyQt5.QtGui import QFont, QColor, QPalette, QIcon #type: ignore

API_KEY = ""
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

def custom_exception_hook(exctype, value, tb):
    print(f"An unhandled exception occurred:")
    print(f"Type: {exctype}")
    print(f"Value: {value}")
    import traceback
    traceback.print_exception(exctype, value, tb)
    sys.__excepthook__(exctype, value, tb) 

sys.excepthook = custom_exception_hook

class CadQueryGenerator(QThread):
    generation_finished = pyqtSignal(str)
    generation_error = pyqtSignal(str)
    code_generated = pyqtSignal(str)

    def __init__(self, description: str, api_key: str, existing_cad_code: str = None, image_base64: str = None):
        super().__init__()
        self.description = description
        self.api_key = api_key
        self.existing_cad_code = existing_cad_code
        self.image_base64 = image_base64

    def run(self):
        chat_history = []
        
        cadquery_instruction = "The code must assign the final CadQuery Workplane object to a variable named 'result'. Provide only the Python code, without any additional text, explanations, or markdown code blocks (e.g., ```python). The output should be raw Python code. When using `cadquery.Sketch`, only use methods like `addRect()`, `addCircle()`, `addPolyline()`, `addArc()`. DO NOT use `addCenterSlot()` on `cadquery.Sketch` objects. Instead, use `cq.Workplane.slot()` or combine basic sketch primitives to create slot shapes."

        if self.image_base64:
            prompt = f"Generate CadQuery Python code for a 3D model based on the provided sketch image. {cadquery_instruction}"
            chat_history.append({
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": "image/png", "data": self.image_base64}} # Assuming PNG, adjust if needed
                ]
            })
        elif self.existing_cad_code:
            prompt = f"""Given the following existing CadQuery Python code, modify it based on the new description.
            The goal is to apply the changes described to the existing model.
            Ensure the modified code still assigns the final CadQuery Workplane object to a variable named 'result'.
            {cadquery_instruction}

            Existing CadQuery Code:
            {self.existing_cad_code}

            New Description for Changes: "{self.description}"
            """
            chat_history.append({"role": "user", "parts": [{"text": prompt}]})
        else:
            prompt = f"""Generate CadQuery Python code for the following CAD part description.
            {cadquery_instruction}

            Description: "{self.description}"

            Example of CadQuery code format:
            import cadquery as cq
            result = cq.Workplane("XY").box(10, 20, 30)
            """
            chat_history.append({"role": "user", "parts": [{"text": prompt}]})

        payload = {"contents": chat_history}
        headers = {'Content-Type': 'application/json'}
        url = f"{GEMINI_API_URL}?key={self.api_key}" if self.api_key else GEMINI_API_URL
        
        cad_query_code_str = ""
        try:
            print("Attempting Gemini API call...") 
            response = requests.post(url, headers=headers, data=json.dumps(payload))
            response.raise_for_status() 
            result_json = response.json()
            print("Gemini API call successful.") 

            if result_json.get("candidates") and len(result_json["candidates"]) > 0 and \
               result_json["candidates"][0].get("content") and \
               result_json["candidates"][0]["content"].get("parts") and \
               len(result_json["candidates"][0]["content"]["parts"]) > 0:
                cad_query_code_str = result_json["candidates"][0]["content"]["parts"][0]["text"]
                
                if cad_query_code_str.strip().startswith("```python"):
                    cad_query_code_str = cad_query_code_str.strip()[len("```python"):].strip()
                if cad_query_code_str.strip().endswith("```"):
                    cad_query_code_str = cad_query_code_str.strip()[:-len("```")].strip()

                self.code_generated.emit(cad_query_code_str) 
            else:
                error_msg = 'No CadQuery code generated by the API. Please check the description or API response structure.'
                self.generation_error.emit(error_msg)
                QMessageBox.critical(None, "API Response Error", error_msg)
                return

        except requests.exceptions.HTTPError as e:
            error_msg = f"API Error (HTTP {e.response.status_code}): {e.response.reason}. Please check your API key and its permissions for the Gemini API."
            self.generation_error.emit(error_msg)
            QMessageBox.critical(None, "API Authentication Error", error_msg)
            return
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Network Connection Error: Could not connect to the Gemini API. Please check your internet connection."
            self.generation_error.emit(error_msg)
            QMessageBox.critical(None, "Network Error", error_msg)
            return
        except requests.exceptions.RequestException as e:
            error_msg = f"General Network or API error: {e}"
            self.generation_error.emit(error_msg)
            QMessageBox.critical(None, "Network/API Error", error_msg)
            return
        except json.JSONDecodeError:
            error_msg = "Failed to parse API response. The response might not be valid JSON."
            self.generation_error.emit(error_msg)
            QMessageBox.critical(None, "API Response Parsing Error", error_msg)
            return
        except Exception as e:
            error_msg = f"An unexpected error occurred during API call: {e}"
            self.generation_error.emit(error_msg)
            QMessageBox.critical(None, "Unexpected API Error", error_msg)
            return

        if cad_query_code_str:
            try:
                print("Attempting to execute CadQuery code...")
                exec_globals = {'cq': cq, 'result': None}
                exec(cad_query_code_str, exec_globals)
                print("CadQuery code execution complete.")

                cad_model = exec_globals.get('result')

                if cad_model and isinstance(cad_model, cq.Workplane):
                    temp_stl_file = tempfile.NamedTemporaryFile(suffix=".stl", delete=False)
                    temp_stl_path = temp_stl_file.name
                    temp_stl_file.close()

                    print(f"Exporting STL to: {temp_stl_path} using .val().exportStl()")
                    cad_model.val().exportStl(temp_stl_path)
                    print("STL export successful.")
                    self.generation_finished.emit(temp_stl_path)
                else:
                    self.generation_error.emit(
                        "Generated code did not produce a valid CadQuery model assigned to 'result'."
                        "Please refine your description to ensure the final model is assigned to 'result'."
                        f"\nGenerated code:\n{cad_query_code_str}"
                    )
            except SyntaxError as e:
                self.generation_error.emit(f"Syntax error in generated CadQuery code: {e}"
                                            f"\nGenerated code:\n{cad_query_code_str}")
            except Exception as e:
                self.generation_error.emit(f"Error executing CadQuery code or exporting model: {e}"
                                            f"\nGenerated code:\n{cad_query_code_str}")
        else:
            self.generation_error.emit('No CadQuery code available for execution.')

class ModelViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.plotter = QtInteractor(self)
        self.layout.addWidget(self.plotter.interactor)
        self.plotter.set_background('#2D3748') 
        self.current_mesh = None
        self.show_edges = True 
        self.plotter.show_axes() 

    def load_stl(self, stl_filepath):
        self.plotter.clear()
        self.current_mesh = None 

        try:
            print(f"Loading STL from: {stl_filepath} using PyVista...")
            self.current_mesh = pv.read(stl_filepath)
            print("STL loaded successfully by PyVista.")
            self._add_current_mesh_to_plotter()

            self.plotter.reset_camera()
            self.plotter.render()
            print("Model displayed using PyVista.")

        except Exception as e:
            print(f"Error loading STL with PyVista: {e}")
            QMessageBox.critical(None, "3D Viewer Error", f"Failed to load or display model in PyVista: {e}")
        finally:
            if os.path.exists(stl_filepath) and tempfile.gettempdir() in stl_filepath:
                print(f"Removing temporary STL file: {stl_filepath}")
                os.remove(stl_filepath)
                print("Temporary STL file removed.")

    def _add_current_mesh_to_plotter(self):
        """Helper to add the current mesh with correct settings."""
        if self.current_mesh:
            self.plotter.add_mesh(self.current_mesh, color='#4299E1', show_edges=self.show_edges, edge_color='#A0AEC0', opacity=0.8)

    def clear_model(self):
        self.plotter.clear()
        self.current_mesh = None
        self.plotter.render()
        print("Model viewer cleared.")

    def toggle_mesh(self):
        if self.current_mesh:
            self.show_edges = not self.show_edges
            self.plotter.clear()
            self._add_current_mesh_to_plotter()
            self.plotter.render()
            print(f"Mesh edges toggled to: {self.show_edges}")
        else:
            QMessageBox.information(None, "Toggle Mesh", "No model loaded to toggle mesh.")


class CADChatbotApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AutoModel")
        self.setGeometry(100, 100, 1200, 800)

        self.model_viewer = ModelViewer()
        self.current_cad_code = "" 
        self.generator_thread = None 

        self.model_history = []
        self.history_pointer = -1

        self.init_ui() 
        self._update_undo_redo_buttons() 

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        palette = self.palette()
        palette.setColor(QPalette.Window, QColor("#1E1E1E")) 
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self.model_viewer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.model_viewer.setStyleSheet(
            """
            QWidget {
                border: 1px solid #3A3A3A; /* Slightly lighter border for viewer */
                border-radius: 8px;
                background-color: #2D3748; /* Match plotter background */
            }
            """
        )
        
        self._create_toolbar() 

        left_panel_layout = QVBoxLayout()
        left_panel_layout.setSpacing(15)
        left_panel_widget = QWidget()
        left_panel_widget.setLayout(left_panel_layout)
        left_panel_widget.setStyleSheet(
            """
            QWidget {
                background-color: #282828; /* Darker gray for panels */
                border-radius: 8px;
                padding: 15px;
            }
            """
        )
        main_layout.addWidget(left_panel_widget, 1)


        title_label = QLabel("CAD Prompt")
        title_label.setFont(QFont("Inter", 24, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #E0E0E0;") 
        left_panel_layout.addWidget(title_label)

        self.chat_history_display = QTextEdit()
        self.chat_history_display.setReadOnly(True)
        self.chat_history_display.setFont(QFont("Inter", 10))
        self.chat_history_display.setMinimumHeight(100)
        self.chat_history_display.setStyleSheet(
            """
            QTextEdit {
                border: 1px solid #3A3A3A; /* Darker border */
                border-radius: 8px;
                padding: 10px;
                background-color: #222222; /* Even darker for chat background */
                color: #B0B0B0; /* Light gray for chat history */
            }
            """
        )
        left_panel_layout.addWidget(self.chat_history_display)


        desc_label = QLabel("Part Description:")
        desc_label.setFont(QFont("Inter", 10, QFont.Bold))
        desc_label.setStyleSheet("color: #E0E0E0;") 
        left_panel_layout.addWidget(desc_label)

        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText(
            "e.g., A rectangular box with length 50mm, width 30mm, and height 20mm, "
            "with a 10mm diameter hole drilled through the center of the top face."
            "\n\nOr, if a model is loaded: 'Make the hole larger, 15mm diameter.'"
        )
        self.description_input.setFont(QFont("Inter", 11))
        self.description_input.setMinimumHeight(100) 
        self.description_input.setStyleSheet(
            """
            QTextEdit {
                border: 1px solid #3A3A3A; /* Darker border */
                border-radius: 8px;
                padding: 10px;
                background-color: #222222; /* Dark background for input */
                color: #E0E0E0; /* Light text color */
                selection-background-color: #4299E1; /* Blue selection */
            }
            QTextEdit:focus {
                border: 2px solid #4299E1; /* Blue focus ring */
                outline: none;
            }
            """
        )
        left_panel_layout.addWidget(self.description_input)

        self.generate_button = QPushButton("Generate CAD Model")
        self.generate_button.setFont(QFont("Inter", 12, QFont.Bold))
        self.generate_button.setFixedHeight(45)
        self.generate_button.setStyleSheet(
            """
            QPushButton {
                background-color: #4299E1; /* Blue */
                color: white;
                border-radius: 8px;
                padding: 10px 20px;
                transition: all 0.3s ease;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.2); /* Stronger shadow */
            }
            QPushButton:hover {
                background-color: #3182CE; /* Darker blue on hover */
                box-shadow: 0 6px 8px rgba(0, 0, 0, 0.3);
            }
            QPushButton:pressed {
                background-color: #2B6CB0; /* Even darker blue on press */
                box-shadow: 0 2px 4px rgba(0, 0, 0, 0.4);
            }
            QPushButton:disabled {
                background-color: #2C5282; /* Muted blue for disabled */
                color: #A0AEC0; /* Muted text for disabled */
                cursor: not-allowed;
                box-shadow: none;
            }
            """
        )
        self.generate_button.clicked.connect(self.generate_cad_query)
        left_panel_layout.addWidget(self.generate_button)

        self.status_label = QLabel("")
        self.status_label.setFont(QFont("Inter", 10))
        self.status_label.setStyleSheet("color: #E0E0E0;") 
        self.status_label.setWordWrap(True)
        left_panel_layout.addWidget(self.status_label)

        left_panel_layout.addStretch(1)

        right_panel_layout = QVBoxLayout()
        right_panel_layout.setSpacing(15)
        right_panel_widget = QWidget()
        right_panel_widget.setLayout(right_panel_layout)
        right_panel_widget.setStyleSheet(
            """
            QWidget {
                background-color: #282828; /* Darker gray for panels */
                border-radius: 8px;
                padding: 15px;
            }
            """
        )
        main_layout.addWidget(right_panel_widget, 1)


        model_title_label = QLabel("Generated 3D Model")
        model_title_label.setFont(QFont("Inter", 18, QFont.Bold))
        model_title_label.setAlignment(Qt.AlignCenter)
        model_title_label.setStyleSheet("color: #E0E0E0;") 
        right_panel_layout.addWidget(model_title_label)

        right_panel_layout.addWidget(self.model_viewer)

    def _create_toolbar(self):
        toolbar = self.addToolBar("File")
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        toolbar.setStyleSheet(
            """
            QToolBar {
                background-color: #282828; /* Dark background for toolbar, matching panels */
                spacing: 10px; /* Space between icons */
                padding: 5px;
                border-bottom: 1px solid #3A3A3A; /* Darker border */
            }
            QToolButton {
                background-color: transparent;
                border: none;
                padding: 5px;
                border-radius: 5px;
                color: #E0E0E0; /* Light text color for buttons */
            }
            QToolButton:hover {
                background-color: #3A3A3A; /* Hover effect */
            }
            QToolButton:pressed {
                background-color: #4299E1; /* Blue pressed effect */
            }
            QToolButton:disabled {
                color: #808080; /* Muted text for disabled */
                opacity: 0.5;
            }
            """
        )

        new_action = QAction(QIcon.fromTheme("document-new", QIcon("icons/new_file.png")), "New File", self)
        new_action.triggered.connect(self.new_file)
        toolbar.addAction(new_action)

        open_action = QAction(QIcon.fromTheme("document-open", QIcon("icons/open_file.png")), "Open File", self)
        open_action.triggered.connect(self.open_file)
        toolbar.addAction(open_action)

        save_action = QAction(QIcon.fromTheme("document-save", QIcon("icons/save_file.png")), "Save Model", self)
        save_action.triggered.connect(self.save_model)
        toolbar.addAction(save_action)

        toolbar.addSeparator()

        self.undo_action = QAction(QIcon.fromTheme("edit-undo", QIcon("icons/undo.png")), "Undo", self)
        self.undo_action.triggered.connect(self.undo_model)
        toolbar.addAction(self.undo_action)

        self.redo_action = QAction(QIcon.fromTheme("edit-redo", QIcon("icons/redo.png")), "Redo", self)
        self.redo_action.triggered.connect(self.redo_model)
        toolbar.addAction(self.redo_action)

        toolbar.addSeparator()

        clear_action = QAction(QIcon.fromTheme("edit-clear", QIcon("icons/clear_model.png")), "Clear Model", self)
        clear_action.triggered.connect(self.clear_model)
        toolbar.addAction(clear_action)

        toggle_mesh_action = QAction(QIcon.fromTheme("view-grid", QIcon("icons/toggle_mesh.png")), "Toggle Mesh", self)
        toggle_mesh_action.triggered.connect(self.model_viewer.toggle_mesh)
        toolbar.addAction(toggle_mesh_action)

        toolbar.addSeparator()

        upload_sketch_action = QAction(QIcon.fromTheme("image-x-generic", QIcon("icons/upload_sketch.png")), "Upload Sketch", self)
        upload_sketch_action.triggered.connect(self.upload_sketch)
        toolbar.addAction(upload_sketch_action)

    def _update_undo_redo_buttons(self):
        self.undo_action.setEnabled(self.history_pointer > 0)
        self.redo_action.setEnabled(self.history_pointer < len(self.model_history) - 1)

    def _execute_cad_code_and_display(self, cad_code_str: str):
        if not cad_code_str:
            self.model_viewer.clear_model()
            self.status_label.setText("No CAD code to display.")
            self.status_label.setStyleSheet("color: #E0E0E0;")
            return

        try:
            print("Attempting to execute CadQuery code for display...")
            exec_globals = {'cq': cq, 'result': None}
            exec(cad_code_str, exec_globals)
            print("CadQuery code execution complete for display.")

            cad_model = exec_globals.get('result')

            if cad_model and isinstance(cad_model, cq.Workplane):
                temp_stl_file = tempfile.NamedTemporaryFile(suffix=".stl", delete=False)
                temp_stl_path = temp_stl_file.name
                temp_stl_file.close()

                print(f"Exporting STL to: {temp_stl_path} using .val().exportStl()")
                cad_model.val().exportStl(temp_stl_path)
                print("STL export successful.")
                self.model_viewer.load_stl(temp_stl_path) # Load and display
                self.status_label.setText("Model displayed successfully!")
                self.status_label.setStyleSheet("color: #48BB78;")
            else:
                self.status_label.setText(
                    "Error: Executed code did not produce a valid CadQuery model assigned to 'result'."
                )
                self.status_label.setStyleSheet("color: #F56565;")
        except SyntaxError as e:
            self.status_label.setText(f"Syntax error in CadQuery code: {e}")
            self.status_label.setStyleSheet("color: #F56565;")
        except Exception as e:
            self.status_label.setText(f"Error executing CadQuery code or displaying model: {e}")
            self.status_label.setStyleSheet("color: #F56565;")

    def new_file(self):
        reply = QMessageBox.question(self, 'New File',
                                     "Are you sure you want to start a new file? All unsaved changes will be lost.",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.model_viewer.clear_model()
            self.description_input.clear()
            self.chat_history_display.clear() # Clear chat history
            self.status_label.setText("New session started. Enter a description to generate a new model.")
            self.status_label.setStyleSheet("color: #E0E0E0;") # Neutral color for status
            self.current_cad_code = "" # Reset current CAD code
            self.model_history = [] # Clear history
            self.history_pointer = -1
            self._update_undo_redo_buttons()

    def open_file(self):
        file_dialog = QFileDialog()
        filepath, _ = file_dialog.getOpenFileName(self, "Open STL File", "", "STL Files (*.stl);;All Files (*)")
        if filepath:
            try:
                self.model_viewer.load_stl(filepath)
                self.status_label.setText(f"Loaded model from: {os.path.basename(filepath)}")
                self.status_label.setStyleSheet("color: #48BB78;")
                self.current_cad_code = ""
                self.model_history = [] 
                self.history_pointer = -1
                self._update_undo_redo_buttons()

                self.chat_history_display.append(f"<b style='color:#E0E0E0;'>--- Loaded STL: {os.path.basename(filepath)} ---</b>")
                self.chat_history_display.append("<i style='color:#FFA07A;'>Note: Further prompts will generate NEW models, as direct modification of loaded STLs via CadQuery code is not supported.</i>")
                self.description_input.clear()
            except Exception as e:
                self.status_label.setText(f"Error opening STL file: {e}")
                self.status_label.setStyleSheet("color: #F56565;")

    def save_model(self):
        if self.model_viewer.current_mesh:
            file_dialog = QFileDialog()
            filepath, _ = file_dialog.getSaveFileName(self, "Save Model As", "untitled.stl", "STL Files (*.stl)")
            if filepath:
                try:
                    self.model_viewer.current_mesh.save(filepath)
                    self.status_label.setText(f"Model saved successfully to: {os.path.basename(filepath)}")
                    self.status_label.setStyleSheet("color: #48BB78;")
                except Exception as e:
                    self.status_label.setText(f"Error saving model: {e}")
                    self.status_label.setStyleSheet("color: #F56565;")
        else:
            self.status_label.setText("No model to save.")
            self.status_label.setStyleSheet("color: #F56565;")

    def clear_model(self):
        self.model_viewer.clear_model()
        self.status_label.setText("Model cleared from viewer.")
        self.status_label.setStyleSheet("color: #E0E0E0;") # Neutral color for status
        self.current_cad_code = "" # Also clear the stored CAD code
        self.chat_history_display.clear() # Clear chat history
        self.model_history = [] # Clear history
        self.history_pointer = -1
        self._update_undo_redo_buttons()

    def undo_model(self):
        if self.history_pointer > 0:
            self.history_pointer -= 1
            cad_code_to_load = self.model_history[self.history_pointer]
            self.current_cad_code = cad_code_to_load # Update current code
            self._execute_cad_code_and_display(cad_code_to_load)
            self.chat_history_display.append("<b style='color:#E0E0E0;'>AutoModel:</b> Undoing last change.")
            self.chat_history_display.verticalScrollBar().setValue(self.chat_history_display.verticalScrollBar().maximum())
            self._update_undo_redo_buttons()
        else:
            self.status_label.setText("No more actions to undo.")
            self.status_label.setStyleSheet("color: #FFA07A;")

    def redo_model(self):
        if self.history_pointer < len(self.model_history) - 1:
            self.history_pointer += 1
            cad_code_to_load = self.model_history[self.history_pointer]
            self.current_cad_code = cad_code_to_load # Update current code
            self._execute_cad_code_and_display(cad_code_to_load)
            self.chat_history_display.append("<b style='color:#E0E0E0;'>AutoModel:</b> Redoing last change.")
            self.chat_history_display.verticalScrollBar().setValue(self.chat_history_display.verticalScrollBar().maximum())
            self._update_undo_redo_buttons()
        else:
            self.status_label.setText("No more actions to redo.")
            self.status_label.setStyleSheet("color: #FFA07A;")

    def upload_sketch(self):
        file_dialog = QFileDialog()
        filepath, _ = file_dialog.getOpenFileName(self, "Upload Sketch Image", "", "Image Files (*.png *.jpg *.jpeg)")
        if filepath:
            try:
                with open(filepath, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')

                self.chat_history_display.append(f"<b style='color:#4299E1;'>You:</b> Uploaded sketch: {os.path.basename(filepath)}")
                self.description_input.clear() 

                self.generate_button.setEnabled(False)
                self.generate_button.setText("Generating from Sketch...")
                self.status_label.setText("Generating model from sketch, please wait...")
                self.status_label.setStyleSheet("color: #4299E1;")
                self.model_viewer.plotter.clear()
                self.model_viewer.plotter.render()

                self.generator_thread = CadQueryGenerator(
                    description="Generate 3D model from sketch", 
                    api_key=API_KEY,
                    existing_cad_code=None, 
                    image_base64=encoded_string
                )
                self.generator_thread.generation_finished.connect(self.on_generation_finished)
                self.generator_thread.generation_error.connect(self.on_generation_error)
                self.generator_thread.code_generated.connect(self.on_code_generated)
                self.generator_thread.start()

            except Exception as e:
                self.status_label.setText(f"Error uploading sketch: {e}")
                self.status_label.setStyleSheet("color: #F56565;")
                self.generate_button.setEnabled(True)
                self.generate_button.setText("Generate CAD Model")
                self.chat_history_display.append(f"<b style='color:#F56565;'>AutoModel:</b> Error uploading sketch: {e}")
                self.chat_history_display.verticalScrollBar().setValue(self.chat_history_display.verticalScrollBar().maximum())


    def generate_cad_query(self):
        description = self.description_input.toPlainText().strip()
        if not description:
            self.status_label.setText("Please enter a description for the CAD part.")
            self.status_label.setStyleSheet("color: #F56565;")
            return

        self.chat_history_display.append(f"<b style='color:#4299E1;'>You:</b> {description}") 
        self.description_input.clear() 

        self.generate_button.setEnabled(False)
        self.generate_button.setText("Generating...")
        self.status_label.setText("Generating model, please wait...")
        self.status_label.setStyleSheet("color: #4299E1;") 
        self.model_viewer.plotter.clear()
        self.model_viewer.plotter.render()

        self.generator_thread = CadQueryGenerator(description, API_KEY, self.current_cad_code)
        self.generator_thread.generation_finished.connect(self.on_generation_finished)
        self.generator_thread.generation_error.connect(self.on_generation_error)
        self.generator_thread.code_generated.connect(self.on_code_generated) 
        self.generator_thread.start()

    def on_generation_finished(self, stl_filepath: str):
        self.model_viewer.load_stl(stl_filepath)
        self.status_label.setText("CadQuery model generated and displayed successfully!")
        self.status_label.setStyleSheet("color: #48BB78;") # Green for success
        self.generate_button.setEnabled(True)
        self.generate_button.setText("Generate CAD Model")
        self.generator_thread = None
        self.chat_history_display.append("<b style='color:#48BB78;'>AutoModel:</b> Model generated successfully!") # Green for success message
        self.chat_history_display.verticalScrollBar().setValue(self.chat_history_display.verticalScrollBar().maximum())
        
        if self.current_cad_code:
            if self.history_pointer < len(self.model_history) - 1:
                self.model_history = self.model_history[:self.history_pointer + 1]
            self.model_history.append(self.current_cad_code)
            self.history_pointer = len(self.model_history) - 1
            self._update_undo_redo_buttons()


    def on_code_generated(self, cad_code_str: str):
        self.current_cad_code = cad_code_str
        print("Updated current_cad_code with new generated code.")

    def on_generation_error(self, error_message: str):
        self.status_label.setText(f"Error: {error_message}")
        self.status_label.setStyleSheet("color: #F56565;") 
        self.generate_button.setEnabled(True)
        self.generate_button.setText("Generate CAD Model")
        self.generator_thread = None
        self.chat_history_display.append(f"<b style='color:#F56565;'>AutoModel:</b> Error: {error_message}") # Red for error message
        self.chat_history_display.verticalScrollBar().setValue(self.chat_history_display.verticalScrollBar().maximum())

if __name__ == '__main__':
    pv.set_plot_theme('dark') 

    app = QApplication(sys.argv)
    window = CADChatbotApp()
    window.show()
    sys.exit(app.exec_())
