# SPDX-License-Identifier: GPL-3.0-or-later
"""Ask Claude: describe a change in plain words, Claude proposes a Blender script.

Uses the Claude Code command line tool installed on this computer, signed in with
the user's own Claude plan (Pro and up), so it draws from that plan's usage and
needs no separate API key. Claude never runs anything by itself: it only returns
a Python script, which the user reviews and applies (undoable).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading

import bpy

SYSTEM_PROMPT = """You are the scene assistant inside Blendering, a Blender add-on for product,
furniture and interior visualization. The user (a product designer, usually writing in
Hungarian) describes a change to the current Blender scene. You answer with exactly ONE
fenced ```python block that makes the change, and nothing after it. Inside the block, start
with comment lines (# ...) that explain in the user's language, in 1-3 short sentences,
what the script will do. The user reads them before pressing Apply.

The script runs inside Blender with these names already defined:
  bpy, math, Vector (mathutils), scene (the current scene), bl (the Blendering add-on).

Units are metres in Python (the UI shows millimetres). Z is up.

Blendering functions you should prefer:
  bl.assets.search(kind, text, limit=36) -> list of Poly Haven ids. kind: 'models' or 'hdris'.
      English keywords, e.g. bl.assets.search('models', 'potted plant'), ('models', 'vase'),
      ('models', 'book'), ('hdris', 'city window'), ('hdris', 'studio'). Most popular first.
  bl.assets.add_model(asset_id, scene, location=Vector((x, y, z)), rotation_z=None)
      -> root empty. Real-world size; it drops onto the highest surface below `location`
      (floor, table top, seat), so give x, y and a z a little above the target surface.
      Move/rotate the returned root, never its children. Avoid overlapping the product:
      use the bounding boxes in the scene summary.
  bl.assets.set_hdri(asset_id, scene, strength=1.0, rotation_deg=0.0, visible=True)
  bl.texture_library.assign_wood(objects, key)   keys: "Walnut, oiled (photo)", "Pine, raw (photo)"
  bl.materials.get(key) -> material. Keys: see the summary.
  bl.studio.load_preset(name) -> spec dict; bl.studio.save_preset(name, spec);
  bl.studio.build(spec, subject_objects, scene); bl.studio.relight(spec, subject_objects, scene)
      Spec format: see the example spec in the summary. Lights are placed relative to the
      camera: azimuth negative = camera's left, elevation in degrees, distance and size in
      multiples of the subject's size, power for a 1 m subject, optional "color": [r, g, b]
      (linear) or "temperature" (K), optional "kind": "spot", "aim": "wall", "aim_offset".
  bl.post: scene.blendering_post.layers.add() then set .kind (EXPOSURE, CONTRAST, CURVES,
      SATURATION, WARMTH, VIGNETTE, GLOW, SHARPEN), .amount, .opacity, .mask (ALL, PRODUCT,
      BACKGROUND), then bl.post.build_group(scene).

Rules:
- Objects with "rhino": true come from the user's Rhino model. Never change their geometry,
  location or rotation (the next Rhino sync would overwrite it anyway); materials are fine.
- Never delete objects, lights or cameras the user did not ask you to remove.
- Keep the camera unless the user asks for a new view.
- If the request needs a reference image, the user gives its path; you may Read it.
- If the request cannot be done in Blender, return a script containing only comments that
  explain why and what you suggest instead.
"""


def find_claude():
    home = os.path.expanduser("~")
    for c in (shutil.which("claude"), f"{home}/.local/bin/claude", f"{home}/.claude/local/claude",
              "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if c and os.path.exists(c):
            return c
    return None


def _mm(v):
    return [round(x * 1000) for x in v]


def scene_summary(scene):
    from mathutils import Vector
    from . import materials, studio, texture_library
    objs = []
    for o in scene.objects:
        if not o.visible_get() and o.type != 'EMPTY':
            continue
        if o.parent is not None and o.parent.get("blendering_prop"):
            continue  # children of a placed prop
        e = {"name": o.name, "type": o.type,
             "collection": o.users_collection[0].name if o.users_collection else "",
             "location_mm": _mm(o.matrix_world.translation)}
        if o.type == 'MESH':
            pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
            e["bbox_min_mm"] = _mm([min(p[i] for p in pts) for i in range(3)])
            e["bbox_max_mm"] = _mm([max(p[i] for p in pts) for i in range(3)])
            if o.data.materials and o.data.materials[0]:
                e["material"] = o.data.materials[0].get("blendering_key", o.data.materials[0].name)
        if o.get("rhino_file"):
            e["rhino"] = True
        if o.get("blendering_prop"):
            e["prop"] = o["blendering_prop"]
        if o.type == 'LIGHT':
            e["light"] = {"type": o.data.type, "power_w": round(o.data.energy, 1)}
        if o.type == 'CAMERA':
            e["lens_mm"] = o.data.lens
        objs.append(e)
    s = scene.blendering
    return {
        "scene": scene.name,
        "selected": [o.name for o in bpy.context.selected_objects],
        "active": bpy.context.active_object.name if bpy.context.active_object else None,
        "cursor_mm": _mm(scene.cursor.location),
        "camera": scene.camera.name if scene.camera else None,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "studio_preset": s.studio_preset,
        "studio_presets": studio.list_presets(),
        "example_spec": studio.load_preset(s.studio_preset) if s.studio_preset in studio.list_presets() else None,
        "hdri": scene.world.get("blendering_hdri") if scene.world else None,
        "material_keys": sorted(set(materials.LIBRARY) | set(texture_library.LIBRARY)),
        "post_layers": [(l.kind, l.mask, round(l.amount, 2)) for l in scene.blendering_post.layers],
        "reference_image": bpy.path.abspath(s.studio_reference) if s.studio_reference else None,
        "objects": objs,
    }


# --------------------------------------------------------------- running

_job = {"running": False, "output": "", "error": "", "code": ""}


def _extract_code(text):
    m = re.findall(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    return m[-1].strip() if m else ""


def ask(request, scene, model=""):
    """Start Claude in the background. Poll ``state()`` for the result."""
    exe = find_claude()
    if exe is None:
        raise RuntimeError("Claude Code is not installed on this computer.")
    if _job["running"]:
        raise RuntimeError("Claude is still working on the previous request.")
    summary = json.dumps(scene_summary(scene), ensure_ascii=False, indent=1)
    prompt = f"Scene summary (JSON):\n{summary}\n\nUser request:\n{request}\n"
    sp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    sp.write(SYSTEM_PROMPT)
    sp.close()
    cmd = [exe, "-p", "--output-format", "text", "--no-session-persistence",
           "--system-prompt-file", sp.name, "--tools", "Read", "--allowedTools", "Read",
           "--strict-mcp-config", "--max-turns", "4"]
    if model:
        cmd += ["--model", model]
    _job.update(running=True, output="", error="", code="")

    def run():
        try:
            p = subprocess.run(cmd + [prompt], capture_output=True, text=True, timeout=600,
                               cwd=os.path.expanduser("~"))
            _job["output"] = p.stdout
            _job["error"] = p.stderr.strip()[-600:] if p.returncode else ""
            _job["code"] = _extract_code(p.stdout)
            if not _job["code"] and not _job["error"]:
                _job["error"] = "Claude did not return a script."
        except subprocess.TimeoutExpired:
            _job["error"] = "Claude took longer than 10 minutes."
        except Exception as exc:
            _job["error"] = str(exc)
        finally:
            _job["running"] = False
            try:
                os.unlink(sp.name)
            except OSError:
                pass

    threading.Thread(target=run, daemon=True).start()


def state():
    return dict(_job)


def apply(code, scene):
    import math
    from mathutils import Vector
    import importlib
    bl = importlib.import_module(__package__)
    ns = {"bpy": bpy, "math": math, "Vector": Vector, "scene": scene, "bl": bl}
    exec(compile(code, "<Claude script>", "exec"), ns)


def clear():
    _job.update(output="", error="", code="")
