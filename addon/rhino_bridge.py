# SPDX-License-Identifier: GPL-3.0-or-later
"""Rhino -> Blender bridge.

Reads a .3dm file with rhino3dm and mirrors it into the Blender scene:

* Rhino layers   -> nested collections under one root collection per file
* Rhino objects  -> mesh objects, built from the render meshes saved in the file
* Rhino blocks   -> one hidden collection per block definition + collection instances
* Rhino units    -> converted to metres (Blender's physical unit), shown as mm

Every Blender object keeps its Rhino ID in ``obj["rhino_id"]``. On a re-sync the
object is found by that ID and only its geometry, transform and collection are
updated, so materials and other Blender-side work survive.
"""

import os

import bpy
from mathutils import Matrix, Vector

ID_KEY = "rhino_id"
FILE_KEY = "rhino_file"
LAYER_KEY = "rhino_layer_id"
IDEF_KEY = "rhino_idef_id"
BLOCKS_ROOT_PREFIX = "Blocks · "

UNIT_TO_METRES = {
    "Microns": 1e-6,
    "Micrometers": 1e-6,
    "Millimeters": 0.001,
    "Centimeters": 0.01,
    "Decimeters": 0.1,
    "Meters": 1.0,
    "Kilometers": 1000.0,
    "Inches": 0.0254,
    "Feet": 0.3048,
    "Yards": 0.9144,
    "Miles": 1609.344,
}


class BridgeError(Exception):
    pass


def _import_rhino3dm():
    try:
        import rhino3dm
    except ImportError as exc:
        raise BridgeError(
            "The rhino3dm library is missing. Install it for Blender's Python "
            "(see the Blendering README)."
        ) from exc
    return rhino3dm


def unit_scale(model):
    name = str(model.Settings.ModelUnitSystem).split(".")[-1]
    return UNIT_TO_METRES.get(name, 0.001)


# ---------------------------------------------------------------- geometry

def _meshes_of(geometry, r):
    """Return the rhino3dm render meshes of a piece of geometry."""
    kind = type(geometry).__name__
    if kind == "Mesh":
        return [geometry]
    if kind == "Brep":
        meshes = []
        for face in geometry.Faces:
            m = face.GetMesh(r.MeshType.Render)
            if m is None:
                m = face.GetMesh(r.MeshType.Any)
            if m is not None:
                meshes.append(m)
        return meshes
    if kind == "Extrusion":
        m = geometry.GetMesh(r.MeshType.Render)
        if m is None:
            m = geometry.GetMesh(r.MeshType.Any)
        return [m] if m is not None else []
    return []


def _build_mesh(name, rmeshes, scale):
    """Turn rhino3dm meshes into one Blender mesh centred on its bounding box.

    Returns (mesh datablock, centre in metres) or (None, None) when empty.
    """
    verts, normals, faces = [], [], []
    for rm in rmeshes:
        base = len(verts)
        n_v = len(rm.Vertices)
        has_normals = len(rm.Normals) == n_v
        for i in range(n_v):
            p = rm.Vertices[i]
            verts.append((p.X * scale, p.Y * scale, p.Z * scale))
            if has_normals:
                n = rm.Normals[i]
                normals.append((n.X, n.Y, n.Z))
            else:
                normals.append(None)
        for i in range(len(rm.Faces)):
            f = rm.Faces[i]
            a, b, c, d = f[0], f[1], f[2], f[3]
            if c == d:
                faces.append((base + a, base + b, base + c))
            else:
                faces.append((base + a, base + b, base + c, base + d))
    if not verts or not faces:
        return None, None

    lo = Vector((min(v[0] for v in verts), min(v[1] for v in verts), min(v[2] for v in verts)))
    hi = Vector((max(v[0] for v in verts), max(v[1] for v in verts), max(v[2] for v in verts)))
    centre = (lo + hi) / 2
    verts = [(v[0] - centre.x, v[1] - centre.y, v[2] - centre.z) for v in verts]

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate(clean_customdata=False)
    mesh.update()
    if all(n is not None for n in normals) and len(normals) == len(mesh.vertices):
        mesh.shade_smooth()
        mesh.normals_split_custom_set_from_vertices(normals)
    return mesh, centre


def _xform_to_matrix(xf, scale):
    m = Matrix((
        (xf.M00, xf.M01, xf.M02, xf.M03 * scale),
        (xf.M10, xf.M11, xf.M12, xf.M13 * scale),
        (xf.M20, xf.M21, xf.M22, xf.M23 * scale),
        (xf.M30, xf.M31, xf.M32, xf.M33),
    ))
    return m


# ------------------------------------------------------------- collections

def _link_only_to(obj, collection):
    for c in list(obj.users_collection):
        if c is not collection:
            c.objects.unlink(obj)
    if collection not in obj.users_collection:
        collection.objects.link(obj)


def _child_collection(parent, name, key, value):
    for c in parent.children:
        if c.get(key) == value:
            if c.name != name and not c.name.startswith(name):
                c.name = name
            return c
    c = bpy.data.collections.new(name)
    c[key] = value
    parent.children.link(c)
    return c


def _find_layer_collection(layer_coll, collection):
    if layer_coll.collection is collection:
        return layer_coll
    for child in layer_coll.children:
        found = _find_layer_collection(child, collection)
        if found:
            return found
    return None


# -------------------------------------------------------------------- sync

class SyncReport:
    def __init__(self):
        self.created = 0
        self.updated = 0
        self.removed = 0
        self.skipped = []

    def summary(self):
        text = f"{self.created} new, {self.updated} updated, {self.removed} removed"
        if self.skipped:
            text += f", {len(self.skipped)} skipped"
        return text


def sync(filepath, scene=None):
    """Mirror a .3dm file into ``scene``. Returns a SyncReport."""
    r = _import_rhino3dm()
    scene = scene or bpy.context.scene
    filepath = os.path.abspath(bpy.path.abspath(filepath))
    if not os.path.isfile(filepath):
        raise BridgeError(f"File not found: {filepath}")

    model = r.File3dm.Read(filepath)
    if model is None:
        raise BridgeError("The file could not be read as a Rhino .3dm file.")

    scale = unit_scale(model)
    report = SyncReport()
    stem = os.path.splitext(os.path.basename(filepath))[0]

    us = scene.unit_settings
    if us.system == 'METRIC' and us.length_unit == 'METERS' and scale <= 0.01:
        us.length_unit = 'MILLIMETERS' if scale < 0.01 else 'CENTIMETERS'

    # Root collections -------------------------------------------------
    root = _child_collection(scene.collection, f"Rhino · {stem}", FILE_KEY, filepath)
    blocks_root = _child_collection(scene.collection, BLOCKS_ROOT_PREFIX + stem, FILE_KEY + "_blocks", filepath)
    lc = _find_layer_collection(bpy.context.view_layer.layer_collection, blocks_root)
    if lc is not None:
        lc.exclude = True

    # Layers -> nested collections --------------------------------------
    layers_by_index = {l.Index: l for l in model.Layers}
    layers_by_id = {str(l.Id): l for l in model.Layers}
    layer_coll = {}

    def collection_for_layer(index):
        if index in layer_coll:
            return layer_coll[index]
        layer = layers_by_index.get(index)
        if layer is None:
            return root
        parent_id = str(layer.ParentLayerId)
        parent_layer = layers_by_id.get(parent_id)
        parent = collection_for_layer(parent_layer.Index) if parent_layer else root
        coll = _child_collection(parent, layer.Name, LAYER_KEY, str(layer.Id))
        layer_coll[index] = coll
        return coll

    # Existing Blender objects from this file, by Rhino ID --------------
    existing = {}
    for obj in bpy.data.objects:
        if obj.get(FILE_KEY) == filepath and ID_KEY in obj:
            existing[obj[ID_KEY]] = obj
    seen = set()

    # Block definitions --------------------------------------------------
    idef_member_ids = set()
    idef_coll = {}
    for idef in model.InstanceDefinitions:
        key = str(idef.Id)
        coll = _child_collection(blocks_root, f"Block · {idef.Name}", IDEF_KEY, key)
        idef_coll[key] = coll
        for gid in idef.GetObjectIds():
            idef_member_ids.add(str(gid))

    def material_for_layer(index):
        layer = layers_by_index.get(index)
        if layer is None:
            return None
        return bpy.data.materials.get(layer.Name)

    def upsert(robj, target_coll, default_name):
        rid = str(robj.Attributes.Id)
        geom = robj.Geometry
        kind = type(geom).__name__
        rname = robj.Attributes.Name or ""
        new_name = rname or default_name
        seen.add(rid)
        obj = existing.get(rid)

        if kind == "InstanceReference":
            block = idef_coll.get(str(geom.ParentIdefId))
            if block is None:
                report.skipped.append(f"{rid}: block definition missing")
                return
            if obj is None or obj.type != 'EMPTY':
                if obj is not None:
                    bpy.data.objects.remove(obj)
                obj = bpy.data.objects.new(rname or block.name.replace("Block · ", ""), None)
                obj[ID_KEY] = rid
                obj[FILE_KEY] = filepath
                obj.empty_display_size = 0.02
                report.created += 1
            else:
                report.updated += 1
            obj.instance_type = 'COLLECTION'
            obj.instance_collection = block
            obj.matrix_world = _xform_to_matrix(geom.Xform, scale)
            _link_only_to(obj, target_coll)
            return

        rmeshes = _meshes_of(geom, r)
        if not rmeshes:
            report.skipped.append(f"{rid}: {kind} has no render mesh")
            return

        mesh, centre = _build_mesh(new_name, rmeshes, scale)
        if mesh is None:
            report.skipped.append(f"{rid}: empty mesh")
            return

        if obj is None or obj.type != 'MESH':
            if obj is not None:
                bpy.data.objects.remove(obj)
            obj = bpy.data.objects.new(new_name, mesh)
            obj[ID_KEY] = rid
            obj[FILE_KEY] = filepath
            mat = material_for_layer(robj.Attributes.LayerIndex)
            if mat is not None:
                mesh.materials.append(mat)
            report.created += 1
        else:
            old = obj.data
            for mat in old.materials:
                mesh.materials.append(mat)
            obj.data = mesh
            if old.users == 0:
                bpy.data.meshes.remove(old)
            if rname and obj.name != rname:
                obj.name = rname
            report.updated += 1

        obj.matrix_world = Matrix.Translation(centre)
        _link_only_to(obj, target_coll)

    # Block contents first, so instances have something to point at
    for idef in model.InstanceDefinitions:
        coll = idef_coll[str(idef.Id)]
        for gid in idef.GetObjectIds():
            robj = model.Objects.FindId(gid)
            if robj is not None:
                upsert(robj, coll, idef.Name)

    # Top-level model objects
    for robj in model.Objects:
        if str(robj.Attributes.Id) in idef_member_ids:
            continue
        layer = layers_by_index.get(robj.Attributes.LayerIndex)
        upsert(robj, collection_for_layer(robj.Attributes.LayerIndex),
               layer.Name if layer is not None else "Rhino object")

    # Objects deleted in Rhino
    for rid, obj in existing.items():
        if rid not in seen:
            bpy.data.objects.remove(obj)
            report.removed += 1

    # Block collections that no longer exist
    for coll in list(blocks_root.children):
        if coll.get(IDEF_KEY) not in idef_coll and not coll.objects:
            bpy.data.collections.remove(coll)

    return report
