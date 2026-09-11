(kicadstamp-config
  (trees
    (tree
      (name "tpl")
      (pivot-xy 0.0 0.0)
      (node
        (ref "tpl_root")
        (kind placement)
        (xy 0.0 0.0)
        (node
          (ref "HOST")
          (kind mount)
          (anchor
            (role "HOST")
            (sheet "Own")
            (cluster "GRP")
          )
          (node
            (ref "tpl_own")
            (kind placement)
            (xy 1.0 0.0)
          )
        )
        (node
          (ref "FOREIGN")
          (kind mount)
          (anchor
            (role "FOREIGN")
            (sheet "Shared")
            (cluster "GRP")
          )
          (node
            (ref "tpl_foreign")
            (kind placement)
            (xy 0.0 1.0)
          )
        )
        (node
          (ref "/Own/GRP/+3V3")
          (kind net_trace)
        )
      )
    )
    (tree
      (name "parent")
      (anchor
        (origin)
      )
      (node
        (ref "tpl")
        (kind module)
      )
      (node
        (ref "tpl_a")
        (kind module)
        (rotation 90.0)
      )
      (node
        (ref "tpl_b")
        (kind module)
        (rotation 180.0)
      )
    )
  )
  (cells
    (cell
      "root_cell"
      (components
        (component
          (role "R1")
        )
        (component
          (role "R2")
        )
      )
    )
    (cell
      "sub_a"
      (components
        (component
          (role "S1")
        )
        (component
          (role "S2")
        )
        (component
          (role "S3")
        )
      )
    )
  )
  (entities
    (entity
      (name "tpl_root")
      (cell "root_cell")
      (sheet "Own")
      (cluster "GRP")
    )
    (entity
      (name "tpl_own")
      (cell "sub_a")
      (sheet "Own")
      (cluster "GRP")
    )
    (entity
      (name "tpl_foreign")
      (cell "sub_a")
      (sheet "Own")
      (cluster "GRP")
    )
  )
  (net_traces
    (net_trace
      (net "/Own/GRP/+3V3")
      (anchor_role "HOST")
      (anchor_sheet "Own")
      (tracks
        (track
          (end_along_mm 2.0)
          (net "/Own/GRP/+3V3")
        )
      )
      (vias
        (via
          (offset_along_mm 2.0)
          (net "/Own/GRP/+3V3")
        )
      )
    )
  )
  (tree_instances
    (tree_instance
      (template "tpl")
      (name "tpl_a")
      (sheet "Own_a")
      (cluster "GRP_a")
    )
    (tree_instance
      (template "tpl")
      (name "tpl_b")
      (sheet "Own_b")
      (cluster "GRP_b")
    )
  )
)
