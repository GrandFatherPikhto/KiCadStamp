(kicadstamp-config
  (layer "B.Cu")
  (thermal_via_arrays
    (thermal_via_array
      (name "ic1_thermal")
      (anchor_ref "IC1")
      (pad "145")
    )
  )
  (cells
    (cell
      "cap_pair_standard"
      (vias
        (via
          (offset_across_mm -1.5)
        )
      )
      (components
        (component
          (role "LIGHT")
          (offset_along_mm 1.0)
          (offset_across_mm -1.0)
          (angle_deg 90.0)
          (vias
            (via
              (offset_across_mm -1.0)
              (net "GND")
            )
          )
        )
        (component
          (role "HEAVY")
          (offset_along_mm 1.0)
          (offset_across_mm 2.0)
          (angle_deg 270.0)
          (vias
            (via
              (offset_across_mm 1.3)
              (net "GND")
            )
          )
        )
      )
    )
  )
  (rules
    (rule
      (net "+1V2_VCCINT")
      (name "+1V2_VCCINT")
      (anchor_ref "IC1")
      (spokes
        (spoke
          (pad "109")
          (cell "cap_pair_standard")
          (rotation_deg 90.0)
        )
        (spoke
          (pad "62")
          (cell "cap_pair_standard")
          (shift_x_mm 0.4)
          (rotation_deg 270.0)
        )
      )
    )
  )
)
