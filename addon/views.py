# SPDX-License-Identifier: GPL-3.0-or-later
"""Views: camera states, like Twinmotion's views.

Every camera can remember the state of the scene it was set up with: where the
lights, the backdrop and any tracked objects stand, how the lights are set,
the world, the exposure and the frame size. Jumping to a camera brings all of
that back, so each shot only needs fine-tuning, not setting up again.

What is remembered for a view ("tracked" objects):

* every light,
* the Blendering studio (its collection, or objects tagged ``blendering_studio``),
* any object tagged ``blendering_track`` (e.g. the product's root empty, so a
  product can be turned per view).

The state is stored as JSON in the camera's ``blendering_view`` property, so it
is saved with the .blend file.

"Studio follows camera": while it is on, moving the active camera around the
subject turns the tracked studio around the subject with it, so the backdrop
and the lights keep facing the camera. Lights you move by hand keep your change.
"""

import json
import math

import bpy
from mathutils import Matrix, Vector

from . import studio

VIEW_KEY = "blendering_view"
TRACK_KEY = "blendering_track"
VERSION = 1


# ------------------------------------------------------------------ helpers

def _is_studio(o):
    if o.get(studio.STUDIO_KEY) is not None:
        return True
    coll = bpy.data.collections.get(studio.COLL_NAME)
    return bool(coll and o.name in coll.all_objects)


def tracked_objects(scene):
    """Objects whose state belongs to a view (cameras never)."""
    out = []
    for o in scene.objects:
        if o.type == 'CAMERA':
            continue
        if o.type == 'LIGHT' or o.get(TRACK_KEY) or _is_studio(o):
            out.append(o)
    return out


def _roots(objs):
    names = {o.name for o in objs}
    return [o for o in objs if o.parent is None or o.parent.name not in names]


def subject_pivot(scene):
    """Centre of the visible meshes that are not part of the studio."""
    skip = {o.name for o in tracked_objects(scene)}
    lo = Vector((1e18,) * 3)
    hi = Vector((-1e18,) * 3)
    found = False
    for o in scene.objects:
        if o.type != 'MESH' or o.name in skip or not o.visible_get():
            continue
        for c in o.bound_box:
            p = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
            found = True
    return (lo + hi) / 2 if found else Vector((0.0, 0.0, 0.0))


def _pivot(scene):
    cam = scene.camera
    data = read_view(cam) if cam else None
    if data and "pivot" in data:
        return Vector(data["pivot"])
    return subject_pivot(scene)


def _azimuth(scene, cam=None):
    """Camera azimuth around the pivot in degrees (0 = from the front, -Y; CCW positive)."""
    cam = cam or scene.camera
    d = cam.matrix_world.translation - _pivot(scene)
    return math.degrees(math.atan2(d.x, -d.y))


def _rot_about(pivot, deg):
    return (Matrix.Translation(pivot) @ Matrix.Rotation(math.radians(deg), 4, 'Z')
            @ Matrix.Translation(-pivot))


def _background(scene):
    w = scene.world
    if w is None or not w.use_nodes:
        return None, None
    nodes = w.node_tree.nodes
    bg = next((n for n in nodes if n.type == 'BACKGROUND'), None)
    mapping = next((n for n in nodes if n.type == 'MAPPING'), None)
    return bg, mapping


def _flat(m):
    return [round(v, 6) for row in m for v in row]


def _unflat(f):
    return Matrix([f[0:4], f[4:8], f[8:12], f[12:16]])


# ------------------------------------------------------------ read / write

def read_view(cam):
    raw = cam.get(VIEW_KEY) if cam else None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def capture(scene, cam=None):
    """The current scene state as a view dict."""
    cam = cam or scene.camera
    old = read_view(cam) or {}
    pivot = Vector(old["pivot"]) if "pivot" in old else subject_pivot(scene)
    data = {"version": VERSION, "pivot": list(pivot), "objects": {}, "lights": {}}
    for o in tracked_objects(scene):
        data["objects"][o.name] = {"m": _flat(o.matrix_basis),
                                   "hide_render": o.hide_render,
                                   "hide_viewport": o.hide_viewport}
        if o.type == 'LIGHT':
            L = o.data
            e = {"energy": L.energy, "color": list(L.color)}
            if L.type == 'AREA':
                e.update(shape=L.shape, size=L.size, size_y=L.size_y, spread=L.spread)
            elif L.type == 'SPOT':
                e.update(spot_size=L.spot_size, spot_blend=L.spot_blend,
                         shadow_soft_size=L.shadow_soft_size)
            elif L.type in {'POINT', 'SUN'}:
                e.update(shadow_soft_size=getattr(L, "shadow_soft_size", 0.0),
                         angle=getattr(L, "angle", 0.0))
            data["lights"][o.name] = e
    bg, mapping = _background(scene)
    if bg is not None:
        data["world"] = {"color": list(bg.inputs["Color"].default_value),
                         "strength": bg.inputs["Strength"].default_value}
        if mapping is not None:
            data["world"]["rotation_z"] = mapping.inputs["Rotation"].default_value[2]
    r, v = scene.render, scene.view_settings
    data["render"] = {"resolution_x": r.resolution_x, "resolution_y": r.resolution_y,
                      "film_transparent": r.film_transparent,
                      "exposure": v.exposure, "look": v.look}
    data["azimuth"] = _azimuth(scene, cam) if cam else 0.0
    return data


def store(scene, cam=None):
    cam = cam or scene.camera
    cam[VIEW_KEY] = json.dumps(capture(scene, cam))
    _rebase(scene)


def apply(scene, cam):
    """Bring back the scene state remembered by ``cam``."""
    data = read_view(cam)
    _state["busy"] = True
    try:
        scene.camera = cam
        if data:
            for name, st in data.get("objects", {}).items():
                o = scene.objects.get(name)
                if o is None:
                    continue
                o.matrix_basis = _unflat(st["m"])
                o.hide_render = st.get("hide_render", o.hide_render)
                o.hide_viewport = st.get("hide_viewport", o.hide_viewport)
            for name, e in data.get("lights", {}).items():
                o = scene.objects.get(name)
                if o is None or o.type != 'LIGHT':
                    continue
                for k, val in e.items():
                    if hasattr(o.data, k):
                        try:
                            setattr(o.data, k, val)
                        except (TypeError, AttributeError, ValueError):
                            pass
            w = data.get("world")
            bg, mapping = _background(scene)
            if w and bg is not None:
                bg.inputs["Color"].default_value = w["color"]
                bg.inputs["Strength"].default_value = w["strength"]
                if mapping is not None and "rotation_z" in w:
                    rot = mapping.inputs["Rotation"].default_value
                    rot[2] = w["rotation_z"]
            rs = data.get("render", {})
            r, v = scene.render, scene.view_settings
            for k in ("resolution_x", "resolution_y", "film_transparent"):
                if k in rs:
                    setattr(r, k, rs[k])
            if "exposure" in rs:
                v.exposure = rs["exposure"]
            if "look" in rs:
                try:
                    v.look = rs["look"]
                except TypeError:
                    pass
        bpy.context.view_layer.update()
    finally:
        _state["busy"] = False
    _rebase(scene)


# --------------------------------------------------- studio follows camera

# runtime only: the studio pose the follow rotation starts from
_state = {"busy": False, "cam": None, "az": 0.0, "last_az": 0.0, "mats": {}, "world_z": None}


def _rebase(scene):
    cam = scene.camera
    if cam is None:
        _state["cam"] = None
        return
    az = _azimuth(scene, cam)
    _state.update(cam=cam.name, az=az, last_az=az,
                  mats={o.name: o.matrix_world.copy() for o in _roots(tracked_objects(scene))})
    _, mapping = _background(scene)
    _state["world_z"] = mapping.inputs["Rotation"].default_value[2] if mapping else None


def _follow(scene, depsgraph=None):
    s = scene.blendering_views
    cam = scene.camera
    if cam is None:
        return
    if _state["cam"] != cam.name:
        _rebase(scene)
        return
    az = _azimuth(scene, cam)
    pivot = _pivot(scene)
    if abs(az - _state["last_az"]) > 1e-4:
        if not s.follow:
            _rebase(scene)
            return
        delta = az - _state["az"]
        R = _rot_about(pivot, delta)
        _state["busy"] = True
        try:
            for name, m in _state["mats"].items():
                o = scene.objects.get(name)
                if o is not None:
                    o.matrix_world = R @ m
            _, mapping = _background(scene)
            if mapping is not None and _state["world_z"] is not None:
                rot = mapping.inputs["Rotation"].default_value
                rot[2] = _state["world_z"] + math.radians(delta)
        finally:
            _state["busy"] = False
        _state["last_az"] = az
    elif depsgraph is not None:
        # the camera did not move: a tracked object moved by hand keeps the change
        R_inv = _rot_about(pivot, -(_state["last_az"] - _state["az"]))
        for u in depsgraph.updates:
            if not u.is_updated_transform or not isinstance(u.id, bpy.types.Object):
                continue
            o = u.id.original
            if o.name in _state["mats"]:
                _state["mats"][o.name] = R_inv @ o.matrix_world


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    if _state["busy"]:
        return
    try:
        _follow(scene, depsgraph)
    except (ReferenceError, AttributeError):
        _state["cam"] = None


@bpy.app.handlers.persistent
def _on_load(_dummy):
    _state["cam"] = None


# ----------------------------------------------------- fine-tune controls

def _move_camera(scene, matrix):
    cam = scene.camera
    cam.matrix_world = matrix @ cam.matrix_world


def _get_orbit(self):
    sc = bpy.context.scene
    return _azimuth(sc) if sc.camera else 0.0


def _set_orbit(self, value):
    sc = bpy.context.scene
    if sc.camera is None:
        return
    _move_camera(sc, _rot_about(_pivot(sc), value - _azimuth(sc)))


def _get_height(self):
    sc = bpy.context.scene
    return sc.camera.matrix_world.translation.z if sc.camera else 0.0


def _set_height(self, value):
    sc = bpy.context.scene
    if sc.camera is None:
        return
    dz = value - sc.camera.matrix_world.translation.z
    _move_camera(sc, Matrix.Translation((0.0, 0.0, dz)))


def _horizontal(sc):
    d = sc.camera.matrix_world.translation - _pivot(sc)
    d.z = 0.0
    return d


def _get_distance(self):
    sc = bpy.context.scene
    return _horizontal(sc).length if sc.camera else 0.0


def _set_distance(self, value):
    sc = bpy.context.scene
    if sc.camera is None:
        return
    d = _horizontal(sc)
    if d.length < 1e-6:
        return
    _move_camera(sc, Matrix.Translation(d.normalized() * (value - d.length)))


class BlenderingViewSettings(bpy.types.PropertyGroup):
    remember: bpy.props.BoolProperty(
        name="Remember changes",
        description="When you jump to another view, save what you changed on this one first",
        default=True,
    )
    follow: bpy.props.BoolProperty(
        name="Studio follows camera",
        description="Turning the camera around the subject turns the backdrop and the lights with it",
        default=True,
    )
    orbit: bpy.props.FloatProperty(
        name="Orbit", description="Turn the camera around the subject (degrees, 0 = front)",
        get=_get_orbit, set=_set_orbit, soft_min=-180, soft_max=180, step=100, precision=1,
    )
    height: bpy.props.FloatProperty(
        name="Height", description="Camera height", unit='LENGTH',
        get=_get_height, set=_set_height, step=1, precision=3,
    )
    distance: bpy.props.FloatProperty(
        name="Distance", description="Distance from the subject (horizontal)", unit='LENGTH',
        get=_get_distance, set=_set_distance, min=0.01, step=1, precision=3,
    )


# -------------------------------------------------------------- operators

def _scene_cameras(scene):
    return sorted((o for o in scene.objects if o.type == 'CAMERA'), key=lambda o: o.name.lower())


def _view_through(context):
    for area in context.screen.areas if context.screen else []:
        if area.type == 'VIEW_3D':
            r3d = area.spaces.active.region_3d
            if r3d is not None:
                r3d.view_perspective = 'CAMERA'


class BLENDERING_OT_view_go(bpy.types.Operator):
    bl_idname = "blendering.view_go"
    bl_label = "Go to view"
    bl_description = "Look through this camera and bring back its lights, studio and settings"
    bl_options = {'REGISTER', 'UNDO'}

    camera: bpy.props.StringProperty()

    def execute(self, context):
        sc = context.scene
        cam = sc.objects.get(self.camera)
        if cam is None or cam.type != 'CAMERA':
            self.report({'ERROR'}, f"No camera called {self.camera}")
            return {'CANCELLED'}
        cur = sc.camera
        if sc.blendering_views.remember and cur is not None and cur != cam and read_view(cur):
            store(sc, cur)
        apply(sc, cam)
        _view_through(context)
        if not read_view(cam):
            self.report({'INFO'}, f"{cam.name}: no saved state yet - press Save view when it looks right")
        return {'FINISHED'}


class BLENDERING_OT_view_save(bpy.types.Operator):
    bl_idname = "blendering.view_save"
    bl_label = "Save view"
    bl_description = "Remember the lights, studio and settings as they are now for the active camera"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.camera is not None

    def execute(self, context):
        store(context.scene)
        self.report({'INFO'}, f"View saved: {context.scene.camera.name}")
        return {'FINISHED'}


class BLENDERING_OT_view_new(bpy.types.Operator):
    bl_idname = "blendering.view_new"
    bl_label = "New view"
    bl_description = "Copy the active camera with everything as it is now, as a new view to tweak"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Name", default="View")

    @classmethod
    def poll(cls, context):
        return context.scene.camera is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        sc = context.scene
        src = sc.camera
        if sc.blendering_views.remember and read_view(src):
            store(sc, src)
        cam = src.copy()
        cam.data = src.data.copy()
        cam.name = cam.data.name = self.name
        for c in src.users_collection:
            c.objects.link(cam)
        sc.camera = cam
        store(sc, cam)
        _view_through(context)
        return {'FINISHED'}


class BLENDERING_OT_view_forget(bpy.types.Operator):
    bl_idname = "blendering.view_forget"
    bl_label = "Forget view state"
    bl_description = "Remove the remembered state from this camera (the camera itself stays)"
    bl_options = {'REGISTER', 'UNDO'}

    camera: bpy.props.StringProperty()

    def execute(self, context):
        cam = context.scene.objects.get(self.camera)
        if cam is not None and VIEW_KEY in cam:
            del cam[VIEW_KEY]
        return {'FINISHED'}


class BLENDERING_OT_view_track(bpy.types.Operator):
    bl_idname = "blendering.view_track"
    bl_label = "Remember per view"
    bl_description = ("Remember the position of the selected objects in each view "
                      "(e.g. turn the product differently per camera). Lights are always remembered")
    bl_options = {'REGISTER', 'UNDO'}

    off: bpy.props.BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        n = 0
        for o in context.selected_objects:
            if o.type == 'CAMERA':
                continue
            if self.off:
                if TRACK_KEY in o:
                    del o[TRACK_KEY]
            else:
                o[TRACK_KEY] = True
            n += 1
        _rebase(context.scene)
        self.report({'INFO'}, f"{n} object(s) {'no longer' if self.off else 'now'} remembered per view")
        return {'FINISHED'}


# ------------------------------------------------------------------ panel

class BLENDERING_PT_views(bpy.types.Panel):
    bl_label = "Views"
    bl_idname = "BLENDERING_PT_views"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blendering"

    def draw(self, context):
        layout = self.layout
        sc = context.scene
        s = sc.blendering_views

        col = layout.column(align=True)
        cams = _scene_cameras(sc)
        if not cams:
            col.label(text="No cameras yet", icon="INFO")
        for cam in cams:
            row = col.row(align=True)
            saved = bool(cam.get(VIEW_KEY))
            op = row.operator("blendering.view_go", text=cam.name,
                              icon="OUTLINER_OB_CAMERA" if saved else "CAMERA_DATA",
                              depress=(cam == sc.camera))
            op.camera = cam.name
            if saved:
                row.operator("blendering.view_forget", text="", icon="X").camera = cam.name

        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("blendering.view_save", icon="FILE_TICK")
        row.operator("blendering.view_new", icon="ADD")
        layout.prop(s, "remember")
        layout.prop(s, "follow")

        if sc.camera is not None:
            box = layout.box()
            box.label(text="Fine-tune: " + sc.camera.name, icon="VIEW_CAMERA")
            c = box.column(align=True)
            c.prop(s, "orbit", slider=False)
            c.prop(s, "height")
            c.prop(s, "distance")
            c.prop(sc.camera.data, "lens", text="Lens")
            box.operator("blendering.straighten_verticals", icon="CAMERA_DATA")

        row = layout.row(align=True)
        row.label(text="Selected:")
        row.operator("blendering.view_track", text="Remember per view", icon="PINNED")
        row.operator("blendering.view_track", text="", icon="UNPINNED").off = True


classes = (
    BlenderingViewSettings,
    BLENDERING_OT_view_go,
    BLENDERING_OT_view_save,
    BLENDERING_OT_view_new,
    BLENDERING_OT_view_forget,
    BLENDERING_OT_view_track,
    BLENDERING_PT_views,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.blendering_views = bpy.props.PointerProperty(type=BlenderingViewSettings)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_post.append(_on_load)


def unregister():
    for h, lst in ((_on_depsgraph, bpy.app.handlers.depsgraph_update_post),
                   (_on_load, bpy.app.handlers.load_post)):
        if h in lst:
            lst.remove(h)
    del bpy.types.Scene.blendering_views
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
