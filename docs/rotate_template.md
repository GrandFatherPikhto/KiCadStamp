## 📄 `transform_template.py`

## 📌 Usage

### 1️⃣ Rotate a template by 180° and move the origin to the via on net `/Channel_0/DAC/+3V3_CLKVDD`

```bash
python transform_template.py -i dac_pi_filter_P3V3.sexp -o dac_pi_filter_P3V3_rotated.sexp --rotate 180 --set-origin-by-via-net "/Channel_0/DAC/+3V3_CLKVDD"
```

### 2️⃣ Mirror across X and move the origin to the component with role `DAC_PI_FILTER_C1`

```bash
python transform_template.py -i template.sexp -o new.sexp --mirror-x --set-origin-by-component-role DAC_PI_FILTER_C1
```

### 3️⃣ Mirror across Y and rotate by 90°, moving the origin by via index (the first via)

```bash
python transform_template.py -i template.sexp -o new.sexp --mirror-y --rotate 90 --set-origin-by-via-index 0
```

### 4️⃣ Explicit origin shift (not anchored to any element)

```bash
python transform_template.py -i template.sexp -o new.sexp --origin-x 1.5 --origin-y -2.0
```

---

## 🧠 Transform logic

1. **Mirroring** (if enabled) is applied to the **coordinates** (along/across) and the **angles** of
   components:
   - `--mirror-x` → `across = -across`, angle sign flips.
   - `--mirror-y` → `along = -along`, angle sign flips.
2. **Rotation** is applied after mirroring (if `--rotate` is given):
   - Coordinates are rotated counter-clockwise by the given angle.
   - Component angles are increased by that same angle.
3. **Origin shift** (if an anchor element or explicit coordinates are given):
   - The anchor element's coordinates (after mirroring and rotation) are computed.
   - Those coordinates are subtracted from every via and component — the anchor element becomes (0,0).
   - If explicit `--origin-x`/`--origin-y` are given instead, they are subtracted from all coordinates
     (equivalent to a plain shift).

---

## ✅ Output

A new `.sexp` file with the same template name, but with transformed coordinates and angles. All `net`
and other fields are preserved as-is.

---

## 🛠️ Where to put the script

Place it in the project root or in `tools/`. Run it from the command line.

If you ever need to support several templates in one file, the script can be extended — but for this use
case a single template is enough.

---

## 💡 Worked example

Suppose you have a template `dac_pi_filter_P3V3` extracted from `Channel_0`, and you want a template for
`Channel_1` rotated by 180° with the origin moved to the `+3V3_CLKVDD` via (already present in the
template). Then:

```bash
python transform_template.py -i templates/dac_pi_filter_P3V3.sexp -o templates/dac_pi_filter_P3V3_ch1.sexp --rotate 180 --set-origin-by-via-net "/Channel_0/DAC/+3V3_CLKVDD"
```

After this, in the new template the power via sits at (0,0), and every other element is offset relative
to it. You can then use this template for `Channel_1` with the same `anchor_ref` and `origin_x/y`
parameters (no further shift needed).

This should make preparing templates for different orientations much easier!
