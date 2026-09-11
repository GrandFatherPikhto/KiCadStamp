(kicadstamp-config
  (version 1)
  (trees
    (tree
      (name "internal_mount")
      (anchor
        (origin)
      )
      (node
        (ref "E_DAC")
        (kind placement)
        (xy 0.0 0.0)
      )
      (node
        (ref "M_dac_pad3")
        (kind mount)
        (anchor
          (role "DAC")
          (sheet "Channel_0")
          (cluster "DAC_BUF")
          (pad "3")
        )
        (node
          (ref "E_PIF")
          (kind placement)
          (xy 1.0 0.0)
        )
      )
      (node
        (ref "M_ext")
        (kind mount)
        (anchor
          (role "EXT")
          (sheet "Channel_0")
          (cluster "DAC_BUF")
          (pad "1")
        )
        (node
          (ref "E_EXT")
          (kind placement)
          (xy 0.0 2.0)
        )
      )
    )
  )
  (cells
    (cell
      "dac_cell"
      (layer "F.Cu")
      (components
        (component
          (role "DAC")
          (offset_along_mm 0.0)
          (offset_across_mm 0.0)
          (angle_deg 0.0)
        )
        (component
          (role "DAC_ALT")
          (offset_along_mm 2.0)
          (offset_across_mm 0.0)
          (angle_deg 90.0)
        )
      )
    )
    (cell
      "pif_cell"
      (layer "F.Cu")
      (components
        (component
          (role "PIF")
          (offset_along_mm 0.0)
          (offset_across_mm 0.0)
          (angle_deg 0.0)
        )
      )
    )
    (cell
      "ext_cell"
      (layer "F.Cu")
      (components
        (component
          (role "EXT_C")
          (offset_along_mm 0.0)
          (offset_across_mm 0.0)
          (angle_deg 0.0)
        )
      )
    )
  )
  (entities
    (entity
      (name "E_DAC")
      (cell "dac_cell")
      (cluster "DAC_BUF")
      (sheet "Channel_0")
    )
    (entity
      (name "E_PIF")
      (cell "pif_cell")
      (cluster "DAC_BUF")
      (sheet "Channel_0")
    )
    (entity
      (name "E_EXT")
      (cell "ext_cell")
      (cluster "DAC_BUF")
      (sheet "Channel_0")
    )
  )
)
