# The case

A printed case that holds the Launchpad, the Pi with its battery board, the battery, the amplifier with two
speakers and the motion sensor. It has two folding feet and a charging socket in the front wall.

## Which version

**V20 is experimental.** It revises V19 with fixes for the problems found when V19 was fitted:

- the Launchpad did not quite drop into the rim;
- the power button's switch did not go into its pocket;
- the Pi's audio jack touched the back wall;
- the speakers' frames did not sit under their rails as meant.

**V20 has not been printed yet**, so it is untested: the fixes are measured and checked in CAD, not on a printed
part. V19 was printed and assembled despite those problems and is here for reference. Only its shell and lid
differ; every other part is the same in both.

| Folder | What |
|---|---|
| `v20/` | every printable part of V20 |
| `v19/` | the shell and the lid of V19, as printed |
| `v20.py`, `parts/` | the FreeCAD script that makes V20, and what it takes over unchanged from earlier versions |

## Printing

The files lie as they are printed.

| File | What | Notes |
|---|---|---|
| `shell.stl` | the one-piece shell, 265 × 265 × 39 mm | top face down; needs support under the plate and a bed of 270 mm or more |
| `lid.stl` | the bottom lid, 265 × 158 × 25 mm | as it sits; holds both speakers and the battery |
| `clamp.stl`, `clamp_right.stl` | hold the Launchpad down at its sides | on their outer face |
| `foot.stl`, `foot_right.stl` | the folding feet | on their side |
| `pivot_pin.stl`, `pivot_pin_right.stl`, `keeper.stl`, `keeper_right.stl` | the feet's pins and their clips | |

The parts list, the wiring and the order of assembly are in [docs/build.md](../docs/build.md).

## Changing it

The case is licensed [CC BY-NC-SA 4.0](../LICENSE-assets): the generator [v20.py](v20.py), the CAD models and the print files. Print it, change it and share it, with credit and under the same licence. To sell it or prints of it, ask first.

The case is a FreeCAD script (FreeCAD 1.x):

```
freecadcmd case/v20.py
```

It writes `v20/*.stl` again and `v20/case_v20.FCStd`, the whole assembly with the components in place, to look at
in FreeCAD. It also reports collisions between the printed parts and the components. The battery board and the
motion sensor are plain boxes of their outer measurements in `parts/`, so those two are left out of that check.
