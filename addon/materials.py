# SPDX-License-Identifier: GPL-3.0-or-later
"""Procedural material library.

Every material is built from nodes only (no image textures), so it stays sharp
at any size and the colour can be set exactly. ``get(name)`` returns the
material, creating it on first use; an existing material with the same name is
reused so the user's own tweaks are never overwritten.
"""

import math

import bpy

PREFIX = "BL · "


def _srgb(r, g, b):
    """sRGB 0-255 -> linear 0-1 (what Blender colour sockets expect)."""
    def c(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return (c(r), c(g), c(b), 1.0)


def _new(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat, nt, bsdf


def _coords(nt, x=-900):
    tc = nt.nodes.new("ShaderNodeTexCoord")
    tc.location = (x, 0)
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.location = (x + 200, 0)
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    return mp


def _build_pine(name, grain='Z'):
    """Light, natural pine, flat-sawn ("cathedral") grain.

    ``grain`` is the object axis the grain runs along: 'Z' for upright panels,
    'X' or 'Y' for horizontal boards. The ring centre sits outside the board, so
    the faces show long arcs instead of the circles of end grain.
    """
    mat, nt, bsdf = _new(name)
    mp = _coords(nt)
    # rotate so the grain axis becomes the ring axis (Z of the wave texture)
    if grain == 'X':
        mp.inputs["Rotation"].default_value = (0.0, math.radians(90), 0.0)
    elif grain == 'Y':
        mp.inputs["Rotation"].default_value = (math.radians(90), 0.0, 0.0)
    mp.inputs["Location"].default_value = (0.35, 0.12, 0.0)   # ring centre off the board
    mp.inputs["Scale"].default_value = (1.0, 1.0, 0.18)       # long, slow arcs

    warp = nt.nodes.new("ShaderNodeTexNoise")
    warp.location = (-450, 200)
    warp.inputs["Scale"].default_value = 2.0
    warp.inputs["Detail"].default_value = 3.0
    nt.links.new(mp.outputs["Vector"], warp.inputs["Vector"])

    mix_vec = nt.nodes.new("ShaderNodeMix")
    mix_vec.data_type = 'VECTOR'
    mix_vec.location = (-250, 100)
    mix_vec.inputs["Factor"].default_value = 0.04
    nt.links.new(mp.outputs["Vector"], mix_vec.inputs["A"])
    nt.links.new(warp.outputs["Color"], mix_vec.inputs["B"])

    rings = nt.nodes.new("ShaderNodeTexWave")
    rings.location = (-50, 100)
    rings.wave_type = 'RINGS'
    rings.rings_direction = 'Z'
    rings.wave_profile = 'SAW'
    rings.inputs["Scale"].default_value = 22.0
    rings.inputs["Distortion"].default_value = 1.2
    rings.inputs["Detail"].default_value = 1.0
    nt.links.new(mix_vec.outputs["Result"], rings.inputs["Vector"])

    # fine longitudinal streaks
    streak_map = nt.nodes.new("ShaderNodeMapping")
    streak_map.location = (-450, -250)
    streak_map.inputs["Scale"].default_value = (60.0, 60.0, 1.5)
    nt.links.new(mp.outputs["Vector"], streak_map.inputs["Vector"])
    streak = nt.nodes.new("ShaderNodeTexNoise")
    streak.location = (-250, -250)
    streak.inputs["Scale"].default_value = 4.0
    nt.links.new(streak_map.outputs["Vector"], streak.inputs["Vector"])

    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.location = (100, 250)
    el = ramp.color_ramp.elements
    el[0].position = 0.0
    el[0].color = _srgb(236, 214, 188)          # early wood, pale
    el[1].position = 1.0
    el[1].color = _srgb(205, 166, 128)          # late wood line
    el.new(0.72).color = _srgb(232, 207, 178)
    nt.links.new(rings.outputs["Fac"], ramp.inputs["Fac"])

    tint = nt.nodes.new("ShaderNodeMix")
    tint.data_type = 'RGBA'
    tint.blend_type = 'MULTIPLY'
    tint.location = (300, 300)
    tint.inputs["Factor"].default_value = 0.12
    nt.links.new(ramp.outputs["Color"], tint.inputs["A"])
    nt.links.new(streak.outputs["Color"], tint.inputs["B"])
    nt.links.new(tint.outputs["Result"], bsdf.inputs["Base Color"])
    bsdf.location = (550, 0)
    nt.nodes["Material Output"].location = (850, 0)

    bump = nt.nodes.new("ShaderNodeBump")
    bump.location = (300, -200)
    bump.inputs["Strength"].default_value = 0.03
    nt.links.new(rings.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Roughness"].default_value = 0.55
    return mat


def wood_axis(obj):
    """Guess the grain direction of a board.

    Horizontal boards (thinnest along Z) get the grain along their longer side;
    upright boards get vertical grain unless they are clearly wider than tall.
    """
    d = obj.dimensions
    thin = min(range(3), key=lambda i: d[i])
    if thin == 2:
        return 'X' if d.x >= d.y else 'Y'
    width = d.y if thin == 0 else d.x
    return 'Z' if d.z >= 0.6 * width else ('Y' if thin == 0 else 'X')


def _build_felt(name, rgb=(58, 54, 50)):
    """Dense wool felt / carpet: fine speckled colour and a soft fibre bump."""
    mat, nt, bsdf = _new(name)
    mp = _coords(nt)

    fine = nt.nodes.new("ShaderNodeTexNoise")
    fine.location = (-450, 200)
    fine.inputs["Scale"].default_value = 900.0
    fine.inputs["Detail"].default_value = 2.0
    nt.links.new(mp.outputs["Vector"], fine.inputs["Vector"])

    weave = nt.nodes.new("ShaderNodeTexWave")
    weave.location = (-450, -100)
    weave.inputs["Scale"].default_value = 260.0
    weave.inputs["Distortion"].default_value = 1.5
    nt.links.new(mp.outputs["Vector"], weave.inputs["Vector"])

    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.location = (-150, 250)
    base = _srgb(*rgb)
    ramp.color_ramp.elements[0].color = tuple(c * 0.55 for c in base[:3]) + (1.0,)
    ramp.color_ramp.elements[1].color = tuple(min(c * 1.6, 1.0) for c in base[:3]) + (1.0,)
    nt.links.new(fine.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])

    mixh = nt.nodes.new("ShaderNodeMath")
    mixh.location = (-150, -100)
    mixh.operation = 'ADD'
    nt.links.new(fine.outputs["Fac"], mixh.inputs[0])
    nt.links.new(weave.outputs["Fac"], mixh.inputs[1])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.location = (100, -200)
    bump.inputs["Strength"].default_value = 0.35
    bump.inputs["Distance"].default_value = 0.002
    nt.links.new(mixh.outputs["Value"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Roughness"].default_value = 1.0
    bsdf.inputs["Sheen Weight"].default_value = 0.4
    return mat


def _build_matte(name, rgb=(40, 40, 42), roughness=0.9):
    mat, nt, bsdf = _new(name)
    bsdf.inputs["Base Color"].default_value = _srgb(*rgb)
    bsdf.inputs["Roughness"].default_value = roughness
    return mat


LIBRARY = {
    "Pine (natural)": lambda n: _build_pine(n, 'Z'),
    "Pine (natural, grain X)": lambda n: _build_pine(n, 'X'),
    "Pine (natural, grain Y)": lambda n: _build_pine(n, 'Y'),
    "Wool felt (charcoal brown)": lambda n: _build_felt(n, (50, 48, 46)),
    "Wall (charcoal)": lambda n: _build_matte(n, (40, 40, 42), 0.92),
    "Paper (white seamless)": lambda n: _build_matte(n, (238, 238, 236), 0.85),
    "Plaster (warm white)": lambda n: _build_matte(n, (226, 220, 210), 0.9),
    "Concrete floor (light)": lambda n: _build_matte(n, (168, 164, 158), 0.8),
}


def assign_wood(objects, key="Pine (natural)"):
    """Give each board the variant of ``key`` whose grain follows its longest side."""
    for obj in objects:
        if obj.type != 'MESH':
            continue
        axis = wood_axis(obj)
        variant = key if axis == 'Z' else f"{key[:-1]}, grain {axis})"
        obj.data.materials.clear()
        obj.data.materials.append(get(variant))


def get(key, rgb=None):
    """Return the library material ``key``; ``rgb`` (sRGB 0-255) overrides its colour."""
    name = PREFIX + key + (f" {rgb[0]}-{rgb[1]}-{rgb[2]}" if rgb else "")
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    if rgb is not None:
        if key.startswith("Wool felt"):
            return _build_felt(name, tuple(rgb))
        return _build_matte(name, tuple(rgb))
    return LIBRARY[key](name)
