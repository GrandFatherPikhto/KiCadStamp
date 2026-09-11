(kicadstamp-config
  (trees
    (tree
      (name "tpl")
      (node
        (ref "tpl_root")
        (kind placement)
        (xy 0.0 0.0)
        (node
          (ref "tpl_own")
          (kind placement)
          (xy 1.0 0.0)
          (anchor
            (role "HOST")
            (sheet "Own")
            (cluster "GRP")
          )
        )
        (node
          (ref "tpl_foreign")
          (kind placement)
          (xy 0.0 1.0)
          (anchor
            (role "FOREIGN")
            (sheet "Shared")
            (cluster "GRP")
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
        (pivot-xy 0.0 0.0)
      )
      (node
        (ref "tpl_a")
        (kind module)
        (pivot-xy 0.0 0.0)
        (rotation 90.0)
      )
      (node
        (ref "tpl_b")
        (kind module)
        (pivot-xy 0.0 0.0)
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
          (start_along_mm 0.0)
          (start_across_mm 0.0)
          (end_along_mm 2.0)
          (end_across_mm 0.0)
          (width_mm 0.25)
          (net "/Own/GRP/+3V3")
        )
      )
      (vias
        (via
          (offset_along_mm 2.0)
          (offset_across_mm 0.0)
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
