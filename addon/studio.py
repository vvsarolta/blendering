# SPDX-License-Identifier: GPL-3.0-or-later
"""Studio builder.

Builds a real 3D photo studio around the subject from a *studio spec*: a small,
JSON-friendly dict describing the backdrop, the lights and the camera. Specs
come from the built-in presets, from saved user presets, or (with the AI layer)
from a written description or a reference photo.

Everything is placed relative to the subject's bounding box, so the same spec
works for a ring and for a sofa. Angles are in degrees:

* camera ``azimuth``: 0 = looking from the front (-Y), 90 = from the right (+X)
* light ``azimuth``: relative to the camera, negative = camera's left
* ``elevation``: 0 = level with the subject's centre, 90 = straight above
"""

import json
import math
import os

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

from . import materials

COLL_NAME = "Blendering · Studio"
STUDIO_KEY = "blendering_studio"


# ----------------------------------------------------------------- presets

BUILTIN_PRESETS = {
    "Dark felt (moody)": {
        "description": "Charcoal wall, brown-grey wool felt floor, one large soft key "
                       "from the front left, deep fall-off to the right. High camera.",
        "backdrop": {"type": "cyc", "corner_radius": 0.25, "wall_distance": 0.85,
                     "floor": "Wool twill, charcoal (photo)", "wall": "Wall (charcoal)"},
        "lights": [
            # raking key from the front left, aimed a little towards the front-left
            # floor: bright floor bottom left, fall-off to the right, wall behind stays dark
            {"role": "key", "azimuth": -62, "elevation": 42, "distance": 1.7,
             "size": 0.9, "aspect": 1.45, "power": 60, "temperature": 5400, "spread": 50,
             "aim_offset": [-0.1, 0.15, -0.1]},
            # background spot: a soft pool of light on the upper right of the wall
            {"role": "background", "kind": "spot", "aim": "wall", "aim_offset": [0.6, 0.0, 1.0],
             "azimuth": 30, "elevation": 50, "distance": 1.3, "spot_size": 70, "blend": 1.0,
             "size": 0.3, "power": 220, "temperature": 5000},
            {"role": "fill", "azimuth": 80, "elevation": 20, "distance": 3.0,
             "size": 2.0, "power": 1.2, "temperature": 5600},
        ],
        "world": {"color": [8, 8, 9], "strength": 0.015},
        "camera": {"azimuth": 28, "elevation": 36, "focal_mm": 70,
                   "aspect": [4, 5], "fill": 0.6, "shift_y": 0.1},
        "exposure": 0.0,
    },
    "White seamless (e-commerce)": {
        "description": "Bright white sweep, soft even light, gentle contact shadow.",
        "backdrop": {"type": "cyc", "corner_radius": 1.2, "wall_distance": 2.0,
                     "floor": "Paper (white seamless)", "wall": "Paper (white seamless)"},
        "lights": [
            {"role": "key", "azimuth": -40, "elevation": 45, "distance": 3.0,
             "size": 3.0, "power": 300, "temperature": 5600},
            {"role": "fill", "azimuth": 50, "elevation": 30, "distance": 3.0,
             "size": 3.0, "power": 150, "temperature": 5600},
            {"role": "top", "azimuth": 0, "elevation": 85, "distance": 3.0,
             "size": 3.0, "power": 120, "temperature": 5600},
        ],
        "world": {"color": [255, 255, 255], "strength": 0.4},
        "camera": {"azimuth": 25, "elevation": 20, "focal_mm": 85,
                   "aspect": [1, 1], "fill": 0.7, "shift_y": 0.0},
        "exposure": 0.0,
    },
    "Warm interior (window light)": {
        "description": "Warm white plaster wall, light concrete floor, big window light "
                       "from the side, warm bounce.",
        "backdrop": {"type": "corner", "corner_radius": 0.0, "wall_distance": 1.8,
                     "floor": "Concrete floor (light)", "wall": "Plaster (warm white)"},
        "lights": [
            {"role": "key", "azimuth": -80, "elevation": 25, "distance": 3.5,
             "size": 3.5, "power": 350, "temperature": 6200},
            {"role": "bounce", "azimuth": 90, "elevation": 10, "distance": 3.5,
             "size": 3.0, "power": 40, "temperature": 3600},
        ],
        "world": {"color": [200, 210, 225], "strength": 0.25},
        "camera": {"azimuth": 20, "elevation": 12, "focal_mm": 50,
                   "aspect": [3, 2], "fill": 0.55, "shift_y": 0.0},
        "exposure": 0.0,
    },
}


def user_preset_dir():
    path = bpy.utils.user_resource('CONFIG', path=os.path.join("blendering", "studios"), create=True)
    return path


def list_presets():
    names = list(BUILTIN_PRESETS)
    d = user_preset_dir()
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            names.append(f[:-5])
    return names


def load_preset(name):
    if name in BUILTIN_PRESETS:
        return json.loads(json.dumps(BUILTIN_PRESETS[name]))
    with open(os.path.join(user_preset_dir(), name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def save_preset(name, spec):
    with open(os.path.join(user_preset_dir(), name + ".json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2, ensure_ascii=False)


# ----------------------------------------------------------------- helpers

def subject_bounds(objects):
    """World-space bounding box (min, max) of the given objects, instances included."""
    deps = bpy.context.evaluated_depsgraph_get()
    pts = []
    names = {o.name for o in objects}
    for inst in deps.object_instances:
        src = inst.instance_object if inst.is_instance else inst.object
        parent = inst.parent if inst.is_instance else inst.object
        if parent is None or parent.name not in names:
            continue
        if src.type != 'MESH':
            continue
        mw = inst.matrix_world
        pts += [mw @ Vector(c) for c in src.bound_box]
    if not pts:
        raise ValueError("No visible mesh in the subject.")
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def _direction(azimuth, elevation):
    a, e = math.radians(azimuth), math.radians(elevation)
    return Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))


def _kelvin_to_rgb(k):
    """Approximate blackbody colour (linear) for a light temperature in Kelvin."""
    t = k / 100.0
    r = 255 if t <= 66 else 329.698727446 * ((t - 60) ** -0.1332047592)
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * ((t - 60) ** -0.0755148492)
    b = 255 if t >= 66 else (0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    rgb = [max(0.0, min(255.0, v)) / 255.0 for v in (r, g, b)]
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb)


def _srgb(c):
    return materials._srgb(*c)[:3]


def _clear_studio(scene):
    coll = bpy.data.collections.get(COLL_NAME)
    if coll is None:
        coll = bpy.data.collections.new(COLL_NAME)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    for o in list(coll.objects):
        data = o.data
        bpy.data.objects.remove(o)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)
            elif isinstance(data, bpy.types.Camera):
                bpy.data.cameras.remove(data)
    return coll


def _backdrop_mesh(width, depth, height, radius, segments=16):
    """Profile: floor from the front edge, a quarter arc, then the wall. Local +Y = away from camera."""
    # wall plane at y = 0, floor at z = 0, arc centre at (y=-r, z=r)
    prof = [(-depth, 0.0), (-radius, 0.0)]
    if radius > 0:
        for i in range(1, segments + 1):
            a = -math.pi / 2 + (math.pi / 2) * i / segments
            prof.append((-radius + radius * math.cos(a), radius + radius * math.sin(a)))
    prof.append((0.0, height))

    bm = bmesh.new()
    left = [bm.verts.new((-width / 2, y, z)) for y, z in prof]
    right = [bm.verts.new((width / 2, y, z)) for y, z in prof]
    inward = Vector((0, -1, 1)).normalized()
    for i in range(len(prof) - 1):
        f = bm.faces.new((left[i], right[i], right[i + 1], left[i + 1]))
        f.normal_update()
        if f.normal.dot(inward) < 0:
            f.normal_flip()
        z_mid = (prof[i][1] + prof[i + 1][1]) / 2
        f.material_index = 0 if z_mid < max(radius, 1e-6) * 0.5 else 1
        f.smooth = radius > 0
    me = bpy.data.meshes.new("Backdrop")
    bm.to_mesh(me)
    bm.free()
    return me


# ------------------------------------------------------------------- build

def _build_lights(spec, centre, size, cam_az, coll, floor_z=None):
    """Lights are placed around the subject, relative to the camera direction.

    ``aim`` = "subject" (default) or "wall" (the backdrop wall behind the subject);
    ``aim_offset`` = [right, towards camera, up] in subject sizes, so a light can be
    pointed at the floor in front of the subject or at a corner of the wall.
    ``kind`` = "area" (softbox, default) or "spot" (``spot_size`` in degrees).
    """
    a = math.radians(cam_az)
    right = Vector((math.cos(a), math.sin(a), 0.0))
    toward = Vector((math.sin(a), -math.cos(a), 0.0))
    wall_dist = spec.get("backdrop", {}).get("wall_distance", 2.0) * size
    wall_point = centre - toward * wall_dist
    if floor_z is not None:
        wall_point.z = floor_z
    for i, ls in enumerate(spec.get("lights", [])):
        role = ls.get("role", f"light {i + 1}")
        dist = ls.get("distance", 3.0) * size
        pos = centre + _direction(cam_az + ls.get("azimuth", 0), ls.get("elevation", 30)) * dist
        base = wall_point if ls.get("aim") == "wall" else centre
        r_off, t_off, u_off = ls.get("aim_offset", [0.0, 0.0, 0.0])
        target = base + (right * r_off + toward * t_off + Vector((0, 0, u_off))) * size
        kind = ls.get("kind", "area")
        light = bpy.data.lights.new(f"{role.title()} light", 'SPOT' if kind == "spot" else 'AREA')
        if kind == "spot":
            light.spot_size = math.radians(ls.get("spot_size", 45))
            light.spot_blend = ls.get("blend", 1.0)
            light.shadow_soft_size = ls.get("size", 0.2) * size
        else:
            light.shape = 'DISK' if ls.get("shape", "softbox") == "round" else 'RECTANGLE'
            light.size = ls.get("size", 2.0) * size
            light.size_y = light.size * ls.get("aspect", 1.0)
            light.spread = math.radians(ls.get("spread", 180))
        # power in the spec is for a 1 m subject; scale with distance squared
        light.energy = ls.get("power", 100) * (size ** 2)
        light.color = _kelvin_to_rgb(ls.get("temperature", 5500))
        ob = bpy.data.objects.new(light.name, light)
        ob.location = pos
        ob.rotation_euler = (target - pos).to_track_quat('-Z', 'Y').to_euler()
        ob[STUDIO_KEY] = role
        coll.objects.link(ob)


def _set_world(spec, scene):
    ws = spec.get("world", {})
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = next((n for n in world.node_tree.nodes if n.type == 'BACKGROUND'), None)
    if bg is not None:
        bg.inputs["Color"].default_value = _srgb(ws.get("color", [10, 10, 10])) + (1.0,)
        bg.inputs["Strength"].default_value = ws.get("strength", 0.3)
    scene.view_settings.exposure = spec.get("exposure", 0.0)


def relight(spec, subject, scene=None):
    """Rebuild only the lights of ``spec``, aimed relative to the scene's current camera."""
    scene = scene or bpy.context.scene
    if scene.camera is None:
        raise ValueError("The scene has no camera. Build the studio first.")
    lo, hi = subject_bounds(subject)
    centre = (lo + hi) / 2
    size = max((hi - lo).length, 1e-3)
    coll = bpy.data.collections.get(COLL_NAME)
    if coll is None:
        coll = bpy.data.collections.new(COLL_NAME)
        scene.collection.children.link(coll)
    for o in list(coll.objects):
        if o.type == 'LIGHT':
            data = o.data
            bpy.data.objects.remove(o)
            if data.users == 0:
                bpy.data.lights.remove(data)
    d = scene.camera.matrix_world.translation - centre
    cam_az = math.degrees(math.atan2(d.x, -d.y))
    backdrop = next((o for o in coll.objects if o.get(STUDIO_KEY) == "backdrop"), None)
    if backdrop is not None:  # use the real wall, it was placed for the original camera
        spec = dict(spec)
        bd = dict(spec.get("backdrop", {}))
        to_wall = backdrop.matrix_world.translation - Vector((centre.x, centre.y, lo.z))
        a = math.radians(cam_az)
        bd["wall_distance"] = max(0.1, to_wall.dot(Vector((-math.sin(a), math.cos(a), 0)))) / size
        spec["backdrop"] = bd
    _build_lights(spec, centre, size, cam_az, coll, lo.z)
    _set_world(spec, scene)
    return coll


def build(spec, subject, scene=None):
    """Build the studio described by ``spec`` around ``subject`` (a list of objects)."""
    scene = scene or bpy.context.scene
    lo, hi = subject_bounds(subject)
    centre = (lo + hi) / 2
    size = max((hi - lo).length, 1e-3)          # bounding-box diagonal
    radius = size / 2
    floor_z = lo.z
    coll = _clear_studio(scene)

    cam_spec = spec.get("camera", {})
    cam_az = cam_spec.get("azimuth", 0.0)

    # Backdrop -----------------------------------------------------------
    bd = spec.get("backdrop", {})
    if bd.get("type", "cyc") != "none":
        r = bd.get("corner_radius", 0.6) * size if bd.get("type") == "cyc" else 0.0
        wall_dist = bd.get("wall_distance", 2.0) * size
        me = _backdrop_mesh(width=size * 14, depth=size * 10, height=size * 7, radius=r)
        me.materials.append(materials.get(bd.get("floor", "Paper (white seamless)")))
        me.materials.append(materials.get(bd.get("wall", "Paper (white seamless)")))
        ob = bpy.data.objects.new("Backdrop", me)
        away = Vector((-math.sin(math.radians(cam_az)), math.cos(math.radians(cam_az)), 0))
        ob.matrix_world = (Matrix.Translation(Vector((centre.x, centre.y, floor_z)) + away * wall_dist)
                           @ Euler((0, 0, math.radians(cam_az))).to_matrix().to_4x4())
        ob[STUDIO_KEY] = "backdrop"
        coll.objects.link(ob)

    _build_lights(spec, centre, size, cam_az, coll, floor_z)
    _set_world(spec, scene)

    # Camera -------------------------------------------------------------
    cam_data = bpy.data.cameras.new("Studio camera")
    cam_data.lens = cam_spec.get("focal_mm", 50)
    cam_data.sensor_fit = 'AUTO'
    cam_data.sensor_width = 36
    ax, ay = cam_spec.get("aspect", [3, 2])
    scene.render.resolution_x = 1600 if ax >= ay else int(1600 * ax / ay)
    scene.render.resolution_y = int(1600 * ay / ax) if ax >= ay else 1600
    half_fov = math.atan((cam_data.sensor_width / 2) / cam_data.lens)
    if ax != ay:  # AUTO fit applies the sensor width to the longer side
        short = min(ax, ay) / max(ax, ay)
        half_fov = math.atan(math.tan(half_fov) * short)
    cam_dist = radius / (cam_spec.get("fill", 0.6) * math.tan(half_fov))
    cam_ob = bpy.data.objects.new("Studio camera", cam_data)
    cam_ob.location = centre + _direction(cam_az, cam_spec.get("elevation", 20)) * cam_dist
    cam_ob.rotation_euler = (centre - cam_ob.location).to_track_quat('-Z', 'Y').to_euler()
    cam_data.shift_y = cam_spec.get("shift_y", 0.0)
    cam_data.clip_end = max(100.0, cam_dist * 20)
    cam_ob[STUDIO_KEY] = "camera"
    coll.objects.link(cam_ob)
    scene.camera = cam_ob

    return coll
