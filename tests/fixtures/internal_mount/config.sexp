(kicadstamp-config
  (version 3)
  (trees
    (tree
      (name "internal_mount")
      (anchor
        (origin)
      )
      (node
        (ref "E_DAC" (uuid "1fb2df30-fc6e-524f-8aa4-48147046035d"))
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
          (ref "E_PIF" (uuid "6cb41b2d-a54f-50b7-8365-17c6db1f7299"))
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
          (ref "E_EXT" (uuid "a7db8b74-57a6-59bc-b269-de157f740eb1"))
          (kind placement)
          (xy 0.0 2.0)
        )
      )
    )
  )
  (cells
    (cell
      "dac_cell"
      (components
        (component
          (role "DAC")
        )
        (component
          (role "DAC_ALT")
          (offset_along_mm 2.0)
          (angle_deg 90.0)
        )
      )
      (uuid "5236cdc4-cb76-5ac9-bb7a-176f1e1e5096")
    )
    (cell
      "pif_cell"
      (components
        (component
          (role "PIF")
        )
      )
      (uuid "6d358c72-86fd-5891-82d6-28fd0060b786")
    )
    (cell
      "ext_cell"
      (components
        (component
          (role "EXT_C")
        )
      )
      (uuid "7141b744-85a3-5a60-8600-ce7ed1d6c895")
    )
  )
  (entities
    (entity
      (name "E_DAC")
      (cell "dac_cell" (uuid "5236cdc4-cb76-5ac9-bb7a-176f1e1e5096"))
      (cluster "DAC_BUF")
      (sheet "Channel_0")
      (uuid "1fb2df30-fc6e-524f-8aa4-48147046035d")
    )
    (entity
      (name "E_PIF")
      (cell "pif_cell" (uuid "6d358c72-86fd-5891-82d6-28fd0060b786"))
      (cluster "DAC_BUF")
      (sheet "Channel_0")
      (uuid "6cb41b2d-a54f-50b7-8365-17c6db1f7299")
    )
    (entity
      (name "E_EXT")
      (cell "ext_cell" (uuid "7141b744-85a3-5a60-8600-ce7ed1d6c895"))
      (cluster "DAC_BUF")
      (sheet "Channel_0")
      (uuid "a7db8b74-57a6-59bc-b269-de157f740eb1")
    )
  )
)
