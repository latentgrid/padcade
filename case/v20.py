# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""The case, V20: V19 with the fixes from fitting the printed V19 (12 Nov 2026). V20 itself has not been printed yet.

- The rim's opening is 0.5 mm longer front to back (the Launchpad didn't quite drop in).
- The speaker rails' lips stand 68 mm apart (V19: 64): the cabinet behind each frame is 67 mm long, so only the
  frame's ends (its "sliders") go under the lips; the grooves take frames up to 72 mm.
- The power button: the switch's pocket has 0.25 mm play a side and 0.3 front to back (V19: 0.1 and 0.05, it didn't
  go in), the tab can travel 1.6 mm (V19 0.7), so its nub, 0.5 mm off the plunger, presses it 0.65 mm past
  touching (the switch travels 0.25). Wide slots behind it for the legs wherever they sit.
- The Pi's edge connectors are in the model (Raspberry Pi's 3A+ drawing: audio jack 2.6 mm past the board, HDMI 1.6,
  micro-USB 1.3; V19 left 1.5 mm to the back wall): the Pi stack, its posts, the battery pocket and the battery
  sit 2.5 mm further forward, so the jack has 1.5 mm to spare.

V19 was:

Changes from V18 (7 Nov 2026):
- One printed shell instead of tray + bay: no frame screws, the speaker grills are whole.
- The top plate is 3 mm instead of 11. The edge (skirt) and the foot housings stay 11 mm deep (the feet fold
  into them, the stand's hooks hold the front edge), and so do two solid strips where the stand's arms touch.
- The stand's arms still pass: the bay keeps V18's two channels for them (V18's measured keep-outs), so the
  bay is V18's outline: a strip along the back, the middle between the channels and a side section (lobe) each
  side that starts at y 167 (V18: 160), so both clamp screws stay in front of the bay, reachable from below.
- Pi, PiSugar, battery, amplifier and sensor sit on posts on the plate's underside, all reachable from the open
  bottom. The Pi lies against the back wall, turned so its USB-A plug and 45 mm bend run along the back strip;
  the battery sits in front of it in a pocket (plate above, walls round it, the lid's ribs below) and is
  plugged into the PiSugar before the Pi goes in.
- The charging socket stays where V18 had it: the panel USB-C board in the front wall of the left side section,
  facing forward, so on the stand the cable leaves downward, out of sight. Its two wires go to the PiSugar's
  5V input pad and GND (no cut cable, nothing plugged in inside); two M2 x 5 self-tapping screws hold it.
- The lid holds both speakers (they slide into rails) and the battery; it goes on 2.5 mm behind its place,
  slides forward so three tongues enter slots in the front walls, and takes three countersunk M3 x 8 at the back.
- The cable to the Launchpad stays inside; only the angled USB-B plug is outside. Its cable drops into a small
  trough on the lid behind the back wall.
- The locating rim round the Launchpad is back, taller (3 mm, V18 1.2) and 1 mm wider left to right (the
  Launchpad is ~1 mm wider than the CAD envelope); front to back it keeps V18's fit. It means support under the
  plate when the shell prints top face down, as V18's tray had.
- The clamps are V18's, unchanged: M3 heat-set inserts, M3 x 8 from below at V18's spots.
- A power button in the back wall: a flexing tab, part of the wall (nothing to fall off), presses a small key
  switch in a fitted pocket behind it; wired to the PiSugar's power-button pad and BAT.
- A wide cutout in the side wall beside each foot, the same shape on the foot's inner side (in place of V18's small
  scoop there) and a finger groove on both faces of the foot.

Assembly coordinates as V18 (z up, y toward the back, Launchpad envelope x 0..259.5, y 0..259.5, z 0..27.3).
The parts that did not change since V18 (clamps, feet, pins, keepers, the foot housings) and the components' shapes
come from parts/parts.FCStd; the battery board and the sensor are plain boxes of their outer measurements there.

    freecadcmd case/v20.py     -> case/v20/*.stl (print orientation, with V18's pins and C-clips)
                                  + case/v20/case_v20.FCStd (parts and components in place)
"""
import os
import shutil
import sys
import FreeCAD as A
import Mesh
import Part

V = A.Vector
HERE = os.path.dirname(os.path.abspath(__file__))
V18_DIR = HERE + '/parts/'
V18 = V18_DIR + 'parts.FCStd'
OUT = HERE + '/v20'

X0, X1, Y0, Y1 = -2.8, 262.3, -2.8, 262.3   # outline (V18's tray and bay), symmetric about x 129.75
MID = (X0 + X1) / 2
CORNER = 8.0
WALL = 2.5
PLATE = 3.0                  # top plate under the Launchpad
SKIRT = 11.0                 # edge depth: the feet fold into it, the stand's hooks hold it
BAY_Y = 125.0                # bay front face (V18's stand keep-out under the front ends here)
FLOOR = -36.0                # lid top
LID = 3.0
ARMS = ((37.25, 72.25), (187.25, 222.25))   # the stand's arms: V18's keep-outs (measured position + margins)
ARMS_END = 220.0             # they reach 210 mm back, + margin
LP_WIDER = 0.5               # the real Launchpad is ~1 mm wider (left to right) than the 259.5 envelope
RIM_H = 3.0                  # locating rim round the Launchpad (V18: 1.2)
RIM_FIT = 0.4                # play inside the rim left to right (V18's)
RIM_FIT_Y = 0.65             # and front to back (V19 0.4: the Launchpad didn't quite drop in)
CLAMP_SCREWS = (93.29, 163.47)  # y: V18's clamps and their M3 inserts, countersunk M3 x 8 from below (in front of the bay)
SELF_TAP = 2.5               # pilot for M3 screws: self-tapping (amplifier) and the lid's countersunk M3 x 8
SELF_TAP_M2 = 1.6            # pilot for M2 self-tapping screws (sensor)
CLEAR = 3.4                  # clearance for M3
LID_SCREWS = (30.0, 165.0, 235.0)   # x, on the back wall
LOBE_Y = 167.0               # the side sections (lobes) start here (V18: 160; 167 keeps the clamps' rear screws
                             # in front of them, reachable from below); the charging socket faces forward there
BUTTON_X, BUTTON_Z = 60.0, -20.0     # power button in the back wall, left of the Pi (PiSugar power-button pad to BAT)
SWITCH = (6.0, 3.5, 5.0)     # the key switch (KHT36PV5-type): width, height, depth with the plunger
SWITCH_LEGS = 6.5
SW_FIT, SW_PLAY = 0.25, 0.3  # the switch's pocket: play a side, and front to back
TAB_TRAVEL = 1.6             # how far the button's tab can go in (at its free edge)
NUB_GAP = 0.2                # nub to plunger with the switch as far forward as its pocket lets it
SW_TIP = 262.3 - 2.5 - TAB_TRAVEL - 0.1 - SW_PLAY - NUB_GAP   # the plunger's tip, switch at the back of its pocket
LID_TABS = ((5.5, LOBE_Y), (160.0, BAY_Y), (254.0, LOBE_Y))   # (x, front wall's y): left lobe, middle, right lobe
SOCKET_X, SOCKET_Z = 19.25, -24.0    # V18's panel USB-C socket (flange 20 x 6.8, M2 holes 16.05 apart)
B_NOTCH = (197.0, 213.0)     # back wall opening under the Launchpad's USB-B socket (plug 12 wide at 199..211)
TROUGH_BACK = 283.0          # the lid's trough reaches this far behind the case (plug sticks out 19 mm)
SPEAKER_FWD = 3.0            # speakers 3 mm forward of V18: the inner back corners are rounded
SPK_C = 222.25 - SPEAKER_FWD  # the speaker's middle (y)
SPK_BODY, SPK_FRAME, SPK_FIT = 67.0, 71.0, 0.5   # cabinet length (measured), longest frame the grooves take, play
BUFFERS = [(x, y) for x in (48.5, 101.0, 219.0) for y in (226.5, 257.5)]    # 8 x 2 mm rubber buffers (V18)
# The Pi + PiSugar stack turns 90 degrees clockwise from V18 (USB-A toward +x) and lies against the back wall:
# its board's edge at x 80.75, the PiSugar's edge (with its USB-C port) 0.5 mm from the back wall.
PI_X, PI_Y = 80.75, 255.8                  # (V19: 258.3)
UNIT_MOVE = V(PI_X - 195.6, PI_Y + 122.0, 0.0)
STACK_UP = 4.5                       # the stack sits higher than V18: ~4.5 mm under the header pins for solder joints
PI_BOLTS = ((PI_X + 3.5, PI_Y - 3.5), (PI_X + 61.5, PI_Y - 3.5), (PI_X + 61.5, PI_Y - 52.5))   # V18's M2.5 axes, turned
FWD = PI_Y - 258.3                   # the Pi stack, the battery and its pocket moved forward from V19 (-2.5)
BATTERY_MOVE = V(-22.0, 5.0 + FWD, 12.8)   # V18's battery -> x 77..132, y 129.5..197, against the plate
AMP_AT = V(-39.0, 420.6, -39.0)      # amp turned upside down (terminals toward the lid), then moved
AMP_HOLES = [(x, y) for x in (148.1, 179.9) for y in (163.1, 194.8)]
SENSOR_MOVE = V(59.7, -22.0, -2.0)
SENSOR_HOLES = [(x, y) for x in (152.54, 172.86) for y in (136.54, 154.36)]


def box(x0, x1, y0, y1, z0, z1):
    return Part.makeBox(x1 - x0, y1 - y0, z1 - z0, V(x0, y0, z0))


def cyl(r, x, y, z0, z1):
    return Part.makeCylinder(r, z1 - z0, V(x, y, z0))


def cyl_x(r, x0, x1, y, z):
    return Part.makeCylinder(r, x1 - x0, V(x0, y, z), V(1, 0, 0))


def rounded_rect(x0, x1, y0, y1, r):
    z = V(0, 0, 1)
    e = [Part.makeLine(V(x0 + r, y0, 0), V(x1 - r, y0, 0)), Part.makeCircle(r, V(x1 - r, y0 + r, 0), z, 270, 360),
         Part.makeLine(V(x1, y0 + r, 0), V(x1, y1 - r, 0)), Part.makeCircle(r, V(x1 - r, y1 - r, 0), z, 0, 90),
         Part.makeLine(V(x1 - r, y1, 0), V(x0 + r, y1, 0)), Part.makeCircle(r, V(x0 + r, y1 - r, 0), z, 90, 180),
         Part.makeLine(V(x0, y1 - r, 0), V(x0, y0 + r, 0)), Part.makeCircle(r, V(x0 + r, y0 + r, 0), z, 180, 270)]
    return Part.Face(Part.Wire(e))


def prism(face, z0, z1):
    f = face.copy()
    f.translate(V(0, 0, z0))
    return f.extrude(V(0, 0, z1 - z0))


def mirror_x(s):
    return s.mirror(V(MID, 0, 0), V(1, 0, 0))


def both_sides(s):
    return [s, mirror_x(s)]


def fuse(shapes):
    s = shapes[0].fuse(shapes[1:]) if len(shapes) > 1 else shapes[0]
    return s.removeSplitter()


def prism_x(points, x0, x1):
    """A polygon in y-z, given as (y, z) pairs, extruded from x0 to x1."""
    return Part.Face(Part.makePolygon([V(x0, y, z) for y, z in points + points[:1]])).extrude(V(x1 - x0, 0, 0))


# lid hooks: a tongue on a post, its underside a 45 degree ramp so it prints without support (lid bottom down);
# the slot in the front wall's boss is the tongue grown by 0.4 mm and runs out at the back, where the tongue
# slides in. The lid goes on 2.5 mm behind its place and slides forward; it cannot swing in (its front edge
# would hit the wall).
HOOK_TONGUE = [(131.7, -32.6), (129.6, -32.6), (129.6, -33.4), (131.7, -35.5)]
HOOK_SLOT = [(133.0, -32.2), (129.2, -32.2), (129.2, -33.6), (132.2, -36.6), (133.0, -36.6)]


def slot_yz(x0, x1, yc, w, z0, z1):
    """A slot through a side wall: w wide in y, round ends at z0 and z1."""
    r = w / 2
    return fuse([box(x0, x1, yc - r, yc + r, z0, z1), cyl_x(r, x0, x1, yc, z0), cyl_x(r, x0, x1, yc, z1)])


OUTLINE = rounded_rect(X0, X1, Y0, Y1, CORNER)
INSIDE = OUTLINE.makeOffset2D(-WALL)


# ---------------------------------------------------------------- components (V18 shapes, moved to V19)

def turned(shape, dz):
    s = shape.copy()
    s.rotate(V(0, 0, 0), V(0, 0, 1), -90)
    s.translate(UNIT_MOVE + V(0, 0, dz))
    return s


def components(doc):
    g = lambda n: doc.getObject(n).Shape
    c = {}
    c['Pi 3A+'] = turned(fuse([g('RPi_PCB_proxy'), g('RPi_USB_A_proxy'), g('RPi_GPIO_proxy')]), STACK_UP)
    # its edge connectors, under the board (parts face the lid), from Raspberry Pi's 3A+ drawing: centre along the
    # edge, width, how far past the board edge, height
    under = -23.6
    c['Pi micro-USB, HDMI, audio jack'] = fuse(
        [box(PI_X + cx - w / 2, PI_X + cx + w / 2, PI_Y - depth, PI_Y + out, under - h, under)
         for cx, w, out, depth, h in ((10.6, 8.4, 1.33, 5.6, 3.0), (32.0, 15.3, 1.62, 11.0, 6.5), (53.5, 7.3, 0.0, 13.0, 6.0))]
        + [Part.makeCylinder(3.15, 2.62, V(PI_X + 53.5, PI_Y, under - 3.0), V(0, 1, 0))])        # the jack's nose
    c['PiSugar 3 Plus'] = turned(g('PiSugar3Plus'), STACK_UP)
    c['Pi side of the stack (measured 13 mm)'] = turned(g('clearance_RPi_MEASURED_13_below_PiSugar_PCB'), STACK_UP)
    c['battery plug and lead'] = turned(fuse([g('clearance_Pi_connector_MEASURED_15_above_PCB'),
                                              g('clearance_battery_lead_ALLOWANCE')]), STACK_UP)
    c['USB-A plug, straight'] = turned(g('USB_A_plug_proxy'), STACK_UP)
    c['battery'] = g('Battery_pouch').translated(BATTERY_MOVE)
    amp = g('Amplifier').copy()
    amp.rotate(V(0, 0, 0), V(1, 0, 0), 180)
    amp.translate(AMP_AT)
    c['amplifier'] = amp
    c['sensor'] = g('BNO085').translated(SENSOR_MOVE)
    q = g('clearance_QT_plug_ALLOWANCE')                # the QT socket on the sensor's left edge this time
    c['sensor plug'] = q.mirror(V(103.0, 0, 0), V(1, 0, 0)).translated(SENSOR_MOVE)
    sp = g('SpeakerLeft').copy()
    sp.translate(V(-2.0, -SPEAKER_FWD, 1.0))          # frame flat on the side wall, top edge on the plate
    sp = sp.fuse(box(2.5, 16.24, SPK_C - SPK_BODY / 2, SPK_C + SPK_BODY / 2, -29.36, -7.36))   # its cabinet: 67 long
    c['left speaker'], c['right speaker'] = both_sides(sp)
    # the plug's bend toward the back, the cable along the back strip to the B notch, up to the B plug
    # (V19's: the plug ends 2.5 mm further forward now, the cable bends toward the back right after it, past the
    # stand channel's back corner at y 222.5)
    c['USB cable inside'] = fuse([box(181.75, 197.0, 222.6, 240.0, -30.7, -20.6), box(197.0, 209.0, 230.0, 262.0, -31.0, -23.0)])
    c['USB cable up to the B plug'] = box(201.0, 209.0, 262.0, 278.0, -31.0, -15.0)
    c['USB-B plug, angled down'] = box(199.0, 211.0, 259.5, 278.5, -15.0, 9.0)
    c['charging socket'] = g('PanelUSB_C').translated(V(0, LOBE_Y - 160.0, 0))   # V18's socket, its wall 7 mm back
    bx, bz, (sw, sh, sd) = BUTTON_X, BUTTON_Z, SWITCH
    c['power button switch'] = fuse([box(bx - sw / 2, bx + sw / 2, SW_TIP - sd, SW_TIP - 1.5, bz - sh / 2, bz + sh / 2),
                                    box(bx - 1.2, bx + 1.2, SW_TIP - 1.5, SW_TIP, bz - 1.0, bz + 1.0)])   # body, plunger
    c['charger plug (outside)'] = box(SOCKET_X - 8.0, SOCKET_X + 8.0, LOBE_Y - 30.0, LOBE_Y, SOCKET_Z - 5.0, SOCKET_Z + 5.0)
    lp = g('Launchpad_envelope')                       # 1 mm wider, as the real one is
    half = box(-10, MID, -10, 270, -5, 40)
    c['Launchpad (1 mm wider)'] = fuse([lp.common(half).translated(V(-LP_WIDER, 0, 0)),
                                       lp.cut(half).translated(V(LP_WIDER, 0, 0)),
                                       lp.common(box(MID - LP_WIDER, MID + LP_WIDER, -10, 270, -5, 40))])
    # V18's stand keep-outs: the stand under the front, and its two arms
    c['stand under the front'] = box(-10, 270, -5, BAY_Y, -61, -SKIRT)
    for i, (x0, x1) in enumerate(ARMS):
        c[f'stand arm {i + 1}'] = box(x0, x1, -5, ARMS_END, -61, -SKIRT)
    return c


# ---------------------------------------------------------------- shell

def recesses(z0, z1, grow=0.0):
    """Where the bay stops (V18's outline): the two channels for the stand's arms and the space in front of
    each side section (lobe); grown by a wall to find the walls round them."""
    (a0, a1), (b0, b1) = ARMS
    return [box(x0 - grow, x1 + grow, BAY_Y - 10, y1 + grow, z0, z1)
            for x0, x1, y1 in ((a0, a1, ARMS_END), (b0, b1, ARMS_END), (X0 - 10, a0, LOBE_Y), (b1, X1 + 10, LOBE_Y))]


def corner(cx, cy, r, sx, sy, z0, z1):
    """What lies between a round of radius r about (cx, cy) and its square corner (towards -sx, -sy), 1 mm beyond."""
    return box(*sorted((cx, cx - sx * (r + 1))), *sorted((cy, cy - sy * (r + 1))), z0, z1).cut(cyl(r, cx, cy, z0 - 1, z1 + 1))


def corners(z0, z1, inside=False):
    """The bay's outside corners seen from below, rounded as V18's: 8 mm at the middle section's front, 4 mm at the
    side sections'. Cut these off outside; fill these (inside=True) into its cavity's corners, so the wall stays WALL
    thick. (The channels' inside corners stay square: a round there would stand in the arms' way.)"""
    (a0, a1), (b0, b1) = ARMS
    return [corner(x + sx * r, y + r, r - WALL if inside else r, sx, 1, z0, z1)
            for x, y, r, sx in ((a1, BAY_Y, 8.0, 1), (b0, BAY_Y, 8.0, -1), (X0, LOBE_Y, 4.0, 1), (a0, LOBE_Y, 4.0, -1),
                                (b1, LOBE_Y, 4.0, 1), (X1, LOBE_Y, 4.0, -1))]


def shell(doc):
    s = prism(OUTLINE, -SKIRT, 0).fuse(prism(OUTLINE, FLOOR, 0).common(box(X0 - 1, X1 + 1, BAY_Y, Y1 + 1, -50, 1)))
    s = s.cut(recesses(FLOOR - 1, -SKIRT))                       # the arms pass under the bay
    (a0, a1), (b0, b1) = ARMS
    front = prism(INSIDE, -SKIRT - 1, -PLATE).common(fuse([box(-10, 270, -10, BAY_Y, -50, 1),
                                                          box(-10, a0, -10, LOBE_Y, -50, 1), box(b1, 270, -10, LOBE_Y, -50, 1)]))
    s = s.cut(front.cut([box(x0, x1, -10, BAY_Y + 1, -50, 1) for x0, x1 in ARMS]))   # solid where the arms touch
    s = s.cut(prism(INSIDE, FLOOR - 1, -PLATE).common(box(-10, 270, BAY_Y + WALL, 270, -50, 1))
              .cut(recesses(-50, 1, WALL)))
    s = s.fuse(corners(FLOOR, -PLATE, inside=True)).cut(corners(FLOOR - 1, -SKIRT))   # round outside, fill inside
    # stiffening ribs under the front of the plate
    s = s.fuse([box(MID - 1, MID + 1, 0, BAY_Y, -SKIRT, -PLATE), box(0, 259.5, 61.0, 63.0, -SKIRT, -PLATE)])
    # the foot housings: V18's tray as it is around each foot (pocket, hinge bore, keeper window, catch)
    tray = doc.getObject('case_tray').Shape
    for region in (box(X0 - 1, 47.5, Y0 - 1, 50.0, -SKIRT - 0.5, 0), box(212.0, X1 + 1, Y0 - 1, 50.0, -SKIRT - 0.5, 0)):
        s = s.cut(region).fuse(tray.common(region))
    # wide finger cutout in the side wall beside each foot, and the same shape on the foot's inner side (14 mm deep:
    # room for a finger from below, where V18 had a smaller scoop)
    grip = lambda x0, x1: fuse([box(x0, x1, 20.0, 42.0, -SKIRT - 1, -6.5), box(x0, x1, 23.0, 39.0, -SKIRT - 1, -3.5),
                                cyl_x(3.0, x0, x1, 23.0, -6.5), cyl_x(3.0, x0, x1, 39.0, -6.5)])
    s = s.cut(both_sides(fuse([grip(X0 - 1, 1.8), grip(27.5, 41.6)])))
    # clamp screws from below: V18's countersunk M3 x 8, heads flush with the plate's underside
    s = s.cut([f for y in CLAMP_SCREWS for x in (3.45, 2 * MID - 3.45)
               for f in (cyl(CLEAR / 2, x, y, -5, 1), Part.makeCone(3.3, CLEAR / 2, 1.6, V(x, y, -PLATE)))])
    # the locating rim: open where the clamps sit, a finger gap at the front (V18's) and the B notch at the back
    opening = rounded_rect(-LP_WIDER - RIM_FIT, 259.5 + LP_WIDER + RIM_FIT, -RIM_FIT_Y, 259.5 + RIM_FIT_Y, 4.69 + RIM_FIT)
    rim = prism(OUTLINE, 0, RIM_H).cut(prism(opening, -1, RIM_H + 1))
    rim = rim.cut([box(X0 - 1, 12.0, 68.0, 189.0, -1, RIM_H + 1), box(2 * MID - 12.0, X1 + 1, 68.0, 189.0, -1, RIM_H + 1),
                   box(124.0, 135.0, Y0 - 1, 5.0, -1, RIM_H + 1)])
    s = s.fuse(rim)
    # speaker rails: the frame (2.4 thick, ~70 long) slides up between the wall and a lip at each end; the lips stand
    # clear of the 67 mm cabinet behind it, so only the frame's ends go under them
    rails = []
    for e in (-1, 1):
        y_lip, y_end = SPK_C + e * (SPK_BODY / 2 + SPK_FIT), SPK_C + e * (SPK_FRAME / 2 + SPK_FIT)
        block = box(-0.4, 4.6, *sorted((y_lip, y_end + 3.0 * e if e < 0 else 259.9)), FLOOR, -PLATE)
        groove = box(-0.4, 2.4, *sorted((y_lip - e, y_end)), FLOOR - 1, -PLATE)
        rails.append(block.cut(groove))
    s = s.fuse(both_sides(fuse(rails)))
    # grill: ten slots in front of each speaker cone, in one piece of wall
    grill = fuse([slot_yz(X0 - 1, 0.5, 197.0 - SPEAKER_FWD + i * 5.6, 3.0, -27.0, -10.0) for i in range(10)])
    s = s.cut(both_sides(grill))
    # charging socket in the left lobe's front wall (V18's cut-out: flange pocket, PCB opening), the wall thickened
    # behind it so two M2 x 5 self-tapping screws hold the flange
    x, y, z = SOCKET_X, LOBE_Y, SOCKET_Z
    s = s.fuse(prism_x([(y + WALL - 0.1, z - 6.5), (y + 6.0, z - 6.5), (y + 6.0, z + 2.9), (y + WALL - 0.1, z + 6.5)],
                       x - 12.25, x + 12.25))                       # its top slopes 45 degrees: no support in print
    s = s.cut([box(x - 10.2, x + 10.2, y - 1, y + 1.6, z - 3.6, z + 3.6), box(x - 6.05, x + 6.05, y - 1, y + 7, z - 3, z + 3)]
              + [Part.makeCylinder(SELF_TAP_M2 / 2, 7, V(x + dx, y - 1, z), V(0, 1, 0)) for dx in (-8.025, 8.025)])
    # opening under the USB-B plug: the cable goes down into the lid's trough and forward into the bay
    s = s.cut(box(*B_NOTCH, 259.3, Y1 + 1, FLOOR - 1, RIM_H + 1))
    # lid screws: bosses on the back wall
    for x in LID_SCREWS:
        s = s.fuse([cyl(3.5, x, 256.3, FLOOR, -PLATE), box(x - 3.5, x + 3.5, 256.3, 259.9, FLOOR, -PLATE)])
        s = s.cut(cyl(SELF_TAP / 2, x, 256.3, FLOOR - 1, -25.0))
    s = power_button(s)
    # lid hooks: bosses with a slot on the bay's front walls
    for x, y in LID_TABS:
        s = s.fuse(box(x - 7, x + 7, y + WALL - 0.1, y + 6.5, FLOOR, -PLATE))
        s = s.cut(prism_x(HOOK_SLOT, x - 5.4, x + 5.4).translated(V(0, y - BAY_Y, 0)))
    # Pi stack: three M2.5 x 12 (5.9 mm of thread in the post) driven from the Pi's side through the Pi, the
    # PiSugar's collar and board into pilot holes in three posts (V18's axes; the fourth corner keeps the kit's own
    # screw). The posts stop on the 0.5 mm collar rings on the PiSugar's board; their last 4 mm are thinner to clear
    # its parts.
    end = -22.0 + STACK_UP
    for x, y in PI_BOLTS:
        s = s.fuse([cyl(4.0, x, y, end + 4.0, -PLATE), cyl(3.0, x, y, end, end + 4.0)])
        s = s.cut(cyl(1.05, x, y, end - 1.0, end + 12.0))
    # battery pocket: plate above, walls round it (a gap at the back left for its lead), the lid's ribs below
    s = s.fuse([w.translated(V(0, FWD, 0)) for w in (
        box(74.0, 76.6, 130.0, 199.9, -13.6, -PLATE), box(74.0, 134.0, 130.0, 131.6, -13.6, -PLATE),
        box(132.4, 134.0, 130.0, 201.5, -13.6, -PLATE), box(90.0, 134.0, 199.9, 201.5, -13.6, -PLATE))])
    # amplifier: four posts, two take M3 x 5 self-tapping screws, two are pins through its holes
    for i, (x, y) in enumerate(AMP_HOLES):
        s = s.fuse(cyl(3.0, x, y, -6.0, -PLATE))
        s = s.fuse(cyl(1.4, x, y, -7.6, -6.0)) if i in (1, 2) else s.cut(cyl(SELF_TAP / 2, x, y, -6.5, -0.6))
    # sensor: four posts (its parts face the plate), two M2 x 5 self-tapping screws, two pins
    for i, (x, y) in enumerate(SENSOR_HOLES):
        s = s.fuse(cyl(2.4, x, y, -8.4, -PLATE))
        s = s.fuse(cyl(1.05, x, y, -10.0, -8.4)) if i in (1, 2) else s.cut(cyl(SELF_TAP_M2 / 2, x, y, -8.9, -0.6))
    s = s.removeSplitter()
    assert s.isValid() and len(s.Solids) == 1, 'shell is not one valid solid'
    return s


def power_button(s):
    """A tab in the back wall, recessed 1.1 mm (found by feel, not pressed by accident), cut free on three sides and
    hinged on its top edge, with TAB_TRAVEL of room behind it. A nub on its back, 13 mm from the hinge, presses the
    switch, which sits in a pocket behind it (SW_FIT play a side, SW_PLAY front to back), legs out the back through
    wide slots, its back against the pocket's back. The nub stops NUB_GAP short of the plunger with the switch as far
    forward as it can go; with it at the back, the tab's travel (1.16 mm at the nub) presses the plunger 0.65 mm past
    touching, against its 0.25 mm of travel. With the shell upside down the switch just drops into the pocket; a post
    on the lid keeps it there. The nub, the dot on the face and the recess's lower edge have 45 degree undersides in
    print."""
    bx, bz, (sw, sh, sd) = BUTTON_X, BUTTON_Z, SWITCH
    x0, x1, z0, z1, gap = bx - 5.0, bx + 5.0, bz - 5.0, bz + 13.0, 0.7      # the tab; hinge at z1
    face, back = Y1 - 1.1, Y1 - WALL                                      # the tab's outer and inner face
    top = bz + sh / 2 + 0.05                                              # the pocket's end toward the plate
    holder = box(x0 - 3, x1 + 3, SW_TIP - sd - 2.0, 259.9, -31.0, -PLATE)
    holder = holder.cut([box(x0 - gap, x1 + gap, back - TAB_TRAVEL, 259.95, z0 - gap, z1 + gap),   # room for the tab
                         box(bx - sw / 2 - SW_FIT, bx + sw / 2 + SW_FIT, SW_TIP - sd, SW_TIP - 1.5 + SW_PLAY, -31.5, top),
                         box(bx - 1.7, bx + 1.7, SW_TIP - 1.6, back - TAB_TRAVEL + 0.1, -31.5, bz + 1.7)]   # plunger, nub
                        + [box(*sorted((bx + e * 2.0, bx + e * 4.6)), SW_TIP - sd - 2.1, SW_TIP - sd + 0.01, -31.5, top)
                           for e in (-1, 1)])                                                  # leg slots
    s = s.fuse(holder)
    zb = z0 - gap - 2.0
    recess = prism_x([(face, z1 + gap), (face, zb), (Y1 + 1, zb - (Y1 + 1 - face)), (Y1 + 1, z1 + gap)], x0 - gap - 2, x1 + gap + 2)
    s = s.cut([recess, box(x0 - gap, x0, 259.0, Y1 + 1, z0 - gap, z1), box(x1, x1 + gap, 259.0, Y1 + 1, z0 - gap, z1),
               box(x0 - gap, x1 + gap, 259.0, Y1 + 1, z0 - gap, z0)])                         # the cut round three sides
    nub = box(bx - 1.05, bx + 1.05, SW_TIP + SW_PLAY + NUB_GAP, back + 0.1, bz - 1.05, bz + 1.05)   # a square at 45
    nub.rotate(V(bx, 0, bz), V(0, 1, 0), 45)
    return s.fuse([nub, Part.makeCone(1.2, 0.0, 1.0, V(bx, face, bz), V(0, 1, 0))])         # nub, dot


# ---------------------------------------------------------------- lid

def lid():
    face = OUTLINE.common(box(X0 - 1, X1 + 1, BAY_Y, Y1 + 1, -1, 1)).cut(recesses(-1, 1) + corners(-1, 1))
    s = prism(face, FLOOR - LID, FLOOR)
    try:
        s = s.makeChamfer(1.2, [e for e in s.Edges if e.BoundBox.ZLength < 1e-6 and abs(e.BoundBox.ZMin - FLOOR + LID) < 1e-6])
    except Part.OCCError:
        pass
    s = s.fuse(box(B_NOTCH[0] - 2, B_NOTCH[1] + 2, Y1 - 1, TROUGH_BACK, FLOOR - LID, FLOOR))   # trough floor
    # trough walls behind the B notch; the cable drops in from the plug
    a, b = B_NOTCH
    s = s.fuse([box(a - 2, a, Y1 + 0.2, TROUGH_BACK, FLOOR, -16.0), box(b, b + 2, Y1 + 0.2, TROUGH_BACK, FLOOR, -16.0),
                box(a - 2, b + 2, TROUGH_BACK - 2, TROUGH_BACK, FLOOR, -16.0)])
    # its back corners rounded, 4 mm outside and 2 inside (the walls are 2 thick)
    ends = ((a + 2, 1), (b - 2, -1))
    s = s.fuse([corner(x, TROUGH_BACK - 4, 2.0, sx, -1, FLOOR, -16.0) for x, sx in ends])
    s = s.cut([corner(x, TROUGH_BACK - 4, 4.0, sx, -1, FLOOR - LID - 1, -15.0) for x, sx in ends])
    # hooks at the front, into the slots of the bay's front walls
    for x, y in LID_TABS:
        s = s.fuse([box(x - 5, x + 5, 131.7 + y - BAY_Y, 133.7 + y - BAY_Y, FLOOR, -32.6),
                    prism_x(HOOK_TONGUE, x - 5, x + 5).translated(V(0, y - BAY_Y, 0))])
    # what the lid holds: the speaker frames and the battery (0.4 mm under it)
    s = s.fuse(both_sides(box(-0.2, 2.0, 192.0 - SPEAKER_FWD, 252.0 - SPEAKER_FWD, FLOOR, -34.2)))   # 0.5 under the frames
    s = s.fuse([box(x - 1, x + 1, 136.0 + FWD, 196.0 + FWD, FLOOR, -13.6) for x in (95.0, 115.0)])
    # a post 0.4 mm under the power button's switch keeps it in its pocket (2 mm: it passes the plunger's channel)
    mid = SW_TIP - (SWITCH[2] + 1.5) / 2                                   # under the middle of the switch's body
    s = s.fuse(box(BUTTON_X - 1.0, BUTTON_X + 1.0, mid - 1.0, mid + 1.0, FLOOR, BUTTON_Z - SWITCH[1] / 2 - 0.4))
    # countersunk holes for M3 x 8 (DIN 7991, 6 mm head) into the back wall's bosses
    for x in LID_SCREWS:
        s = s.cut([cyl(CLEAR / 2, x, 256.3, FLOOR - LID - 1, FLOOR + 1),
                   Part.makeCone(3.3, CLEAR / 2, 3.3 - CLEAR / 2, V(x, 256.3, FLOOR - LID))])
    s = s.removeSplitter()
    assert s.isValid() and len(s.Solids) == 1, 'lid is not one valid solid'
    return s


# ---------------------------------------------------------------- clamps and feet (V18 parts, changed)

def clamp(doc, right=False):
    """V18's clamp as it is (M3 inserts in its two bores)."""
    return doc.getObject('case_clamp_right' if right else 'case_clamp').Shape.copy()


def foot(doc, right=False):
    s = doc.getObject('case_foot').Shape.copy()       # left foot, outer face at x 1.5, inner face at x 27.5
    groove = fuse([g for x in (0.5, 28.5) for g in (Part.makeCylinder(3.0, 18.0, V(x, 22.0, -6.75), V(0, 1, 0)),
                                                    Part.makeSphere(3.0, V(x, 22.0, -6.75)),
                                                    Part.makeSphere(3.0, V(x, 40.0, -6.75)))])
    s = s.cut(groove).removeSplitter()
    assert s.isValid() and len(s.Solids) == 1
    return mirror_x(s) if right else s


# ---------------------------------------------------------------- checks

def overhang(s, up):
    """Area of faces that face the bed more steeply than 45 degrees, printed with `up` as the build direction
    (faces resting on the bed excluded). Bridges count too, so this is a number to look at, not a pass/fail."""
    pts, tris = s.tessellate(0.1)
    low = min(p.dot(up) for p in pts)
    area = 0.0
    for i, j, k in tris:
        n = (pts[j] - pts[i]).cross(pts[k] - pts[i])
        if n.Length > 0 and n.dot(up) / n.Length < -0.72 and min(pts[q].dot(up) for q in (i, j, k)) > low + 0.05:
            area += n.Length / 2
    return area


def check(parts, comps):
    hits = []
    for pn, p in parts.items():
        for cn, c in comps.items():
            if pn.startswith('clamp') and cn.startswith('Launchpad'):
                continue                               # reported on its own below
            if cn in ('PiSugar 3 Plus', 'sensor'):
                continue                               # boxes of their outer measurements here, larger than the
                                                       # real boards: in the assembly to look at, not checked
            v = p.common(c).Volume
            if v > 1e-3:
                hits.append(f'{pn} x {cn}: {v:.2f} mm3')
    names = list(parts)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            v = parts[a].common(parts[b]).Volume
            if v > 1e-3:
                hits.append(f'{a} x {b}: {v:.2f} mm3')
    loose = ['battery', 'amplifier', 'sensor', 'sensor plug', 'USB-A plug, straight', 'USB cable inside',
             'left speaker', 'right speaker', 'Pi side of the stack (measured 13 mm)', 'battery plug and lead',
             'charging socket']
    for i, a in enumerate(loose):
        for b in loose[i + 1:]:
            v = comps[a].common(comps[b]).Volume
            if v > 1e-3:
                hits.append(f'{a} x {b}: {v:.2f} mm3')
    return hits


# ---------------------------------------------------------------- export

def on_bed(s, axis=None, angle=0):
    s = s.copy()
    if axis:
        s.rotate(V(0, 0, 0), axis, angle)
    b = s.BoundBox
    s.translate(V(-b.XMin, -b.YMin, -b.ZMin))
    return s


if os.path.basename(sys.argv[-1]) == 'v20.py':                  # freecadcmd names the module v20
    doc = A.openDocument(V18)
    comps = components(doc)
    parts = {'shell': shell(doc), 'lid': lid(),
             'clamp': clamp(doc), 'clamp right': clamp(doc, True), 'foot': foot(doc), 'foot right': foot(doc, True)}
    for n in ('case_pivot_pin', 'case_pivot_pin_right', 'case_keeper', 'case_keeper_right'):
        parts[doc.getObject(n).Label.replace('Printed ', '')] = doc.getObject(n).Shape.copy()
    hits = check(parts, comps)
    print('collisions:', 'none' if not hits else '\n  ' + '\n  '.join(hits))
    lp = comps['Launchpad (1 mm wider)']
    for n, out in (('clamp', -1), ('clamp right', 1)):       # how far each clamp presses into a 1 mm wider Launchpad
        d = next(i / 100 for i in range(0, 101, 5) if parts[n].translated(V(out * i / 100, 0, 0)).common(lp).Volume < 1e-3)
        print(f'{n}: ' + (f'presses {d:.2f} mm into a Launchpad 1 mm wider than the envelope' if d
                          else f'{parts[n].distToShape(lp)[0]:.2f} mm from a Launchpad 1 mm wider'))
    rim = parts['shell'].common(box(X0 - 1, X1 + 1, Y0 - 1, Y1 + 1, 0.01, RIM_H + 1))
    print(f'rim: {rim.distToShape(lp)[0]:.2f} mm round a Launchpad 1 mm wider, {RIM_H:.1f} mm tall')
    os.makedirs(OUT, exist_ok=True)
    z, y = V(0, 0, 1), V(0, 1, 0)
    # print poses: shell top face down, lid as it sits, clamps on their outer face (as V18),
    # feet on the side V18 printed them
    v18_foot = Mesh.Mesh(V18_DIR + 'foot_mm.stl').BoundBox
    foot_turn = min((90, -90), key=lambda a: abs(on_bed(doc.getObject('case_foot').Shape, y, a).BoundBox.ZLength
                                                  - v18_foot.ZLength) + abs(
        on_bed(doc.getObject('case_foot').Shape, y, a).BoundBox.XLength - v18_foot.XLength))
    poses = {'shell': (V(1, 0, 0), 180, -z), 'lid': (None, 0, z),
             'clamp': (y, -90, V(1, 0, 0)), 'clamp right': (y, 90, V(-1, 0, 0)),
             'foot': (y, foot_turn, None), 'foot right': (y, -foot_turn, None)}
    for n, (axis, angle, up) in poses.items():
        p = on_bed(parts[n], axis, angle)
        Mesh.Mesh(p.tessellate(0.02)).write(f'{OUT}/{n.replace(" ", "_")}.stl')
        b = p.BoundBox
        oh = f', {overhang(parts[n], up):.0f} mm2 facing the bed' if up else ''
        print(f'{n:12s} {b.XLength:6.1f} x {b.YLength:6.1f} x {b.ZLength:5.1f} mm, {parts[n].Volume / 1000:6.1f} cm3{oh}')
    for f in ('pivot_pin', 'pivot_pin_right', 'keeper', 'keeper_right'):        # unchanged V18 prints, for a full set
        shutil.copy(V18_DIR + f'{f}_mm.stl', f'{OUT}/{f}.stl')
    out = A.newDocument('case_v20')
    out.License, out.LicenseURL = doc.License, doc.LicenseURL
    for n, s in list(parts.items()) + list(comps.items()):
        o = out.addObject('Part::Feature', 'p_' + ''.join(ch if ch.isalnum() else '_' for ch in n))
        o.Label, o.Shape = n, s
    out.saveAs(f'{OUT}/case_v20.new.FCStd')                    # (saving over it would leave an .FCBak)
    os.replace(f'{OUT}/case_v20.new.FCStd', f'{OUT}/case_v20.FCStd')
    print('->', OUT)
