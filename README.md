# Solid Studio

Solid Studio is an **AI-only development project** to build a working alternative to Autodesk Fusion 360 on Linux. The project owner sets the direction and AI writes the application code, tests, documentation, and original toolbar icons. The current prototype focuses on the **Design → Solid** workflow.

You can sketch on an origin plane or a flat face, extrude profiles, cut and combine bodies, add fillets and chamfers, and edit earlier features from the timeline. Save editable projects as `.solidstudio` files or export finished solids as STEP. The desktop app uses PySide6 and CadQuery with Open CASCADE. It is an independent project.

The modeling and GUI tests have passed on Ubuntu WSL. Arch Linux is the target platform and still needs a native test run.

## Run on Arch Linux

In a graphical session on x86-64 Arch, open a terminal in the project directory:

```bash
sudo pacman -Syu --needed uv libglvnd libxkbcommon-x11 xcb-util-cursor \
  xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil
uv venv --python 3.13
uv pip install .
.venv/bin/solid-studio
```

The virtual environment uses Python 3.13 independently of Arch's system Python. You can open the example immediately with `.venv/bin/solid-studio examples/mounting-plate.solidstudio`.

## Run on other Linux distributions

On x86-64 Linux with Python 3.10–3.13, a graphical session, and Python's venv support installed, open a terminal in this project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
solid-studio
```

On minimal Debian or Ubuntu systems, Qt may need these desktop runtime libraries:

```bash
sudo apt install libegl1 libgl1 libxkbcommon0 libxkbcommon-x11-0 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0
```

## Try the workflow

1. Open `examples/mounting-plate.solidstudio` with **File → Open**.
2. Double-click **Sketch 1** in the timeline, select the rectangle in the viewport, press **D**, and increase its width. Click **Finish Sketch** to recompute the two cuts and the fillet.
3. Right-click a timeline item to roll before or after it. Use the end button to return to the completed model.
4. Save your edited copy in `.solidstudio` format. Export STEP to use the finished solid in another CAD tool.

For a fresh design, choose **Create Sketch**, then select XY, XZ, or YZ or click a flat face. Choose a tool and start drawing. Three-point Arc takes the start and end first, then a point on the curve. Esc ends the active line chain and preserves its committed segments. Coincident endpoints can form a closed loop for extrusion. Closed loops entirely inside the selected profile become holes. Finish the sketch, press **E**, and choose a profile and distance. Feature dialogs preview their result and report kernel errors before commit.

| Control | Input |
| --- | --- |
| Pan / orbit / zoom | Middle drag / Shift + middle drag / scroll |
| Standard views and fit | Click the view cube or press Home to fit |
| Toolbox | S |
| Sketch line, rectangle, circle, dimension | L, R, C, D |
| Sketch Trim, Offset, Project | T, O, P |
| Toggle construction geometry | X |
| Extrude / fillet | E / F |
| Recompute | Ctrl+B |
| Toggle selected body visibility | V |
| Show/hide Browser, ViewCube, navigation bar | Ctrl+Alt+B, Ctrl+Alt+V, Ctrl+Alt+N |
| Edit a feature | Double-click its timeline item |
| Undo / redo | Ctrl+Z / Ctrl+Shift+Z |

Sketches support endpoint snapping, grid snapping, numeric dimensions, horizontal and vertical constraints on individual straight lines, and construction geometry. Trim operates on individual straight lines crossing other straight segments. Offset supports circles and rectangles. Project copies straight edges from a selected body as construction geometry without a live link. A face sketch resolves its support by plane orientation and proximity during recomputation. Complex topology changes can invalidate or change that match.

The full Fusion sketch toolset still includes features beyond this implementation, including splines, 3D sketches, general geometric constraints, associative projections, and profile resolution for intersecting or branched curves. Solid edge finishing uses top, bottom, or all-edge groups. Fusion document history is unavailable in this format.

Run the model, file round-trip, and GUI interaction checks with `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests`.

When reporting an issue, include your Linux distribution, the command or tool you used, and the steps that reproduce it. A small example design helps with modeling and timeline bugs.
