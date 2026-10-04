(kicadstamp-config
  (version 3)
  (trees
    (tree
      (name "tpl")
      (anchor
        (self)
      )
      (pivot-xy 0.0 0.0)
      (node
        (ref "tpl_root" (uuid "37c9854d-ee0a-57fa-81ff-8561c9fede7e"))
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
            (ref "tpl_own" (uuid "93dc40fa-129b-5b2a-938f-883a5dc69fe1"))
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
            (ref "tpl_foreign" (uuid "4fe1ba9f-514b-5c5c-977e-42fb8a745252"))
            (kind placement)
            (xy 0.0 1.0)
          )
        )
        (node
          (ref "/Own/GRP/+3V3" (uuid "d00c11be-e2c9-5f13-b1d5-dde4835741aa"))
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
      (uuid "9f66e37d-df75-5e24-b6cc-f02ee60d56dc")
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
      (uuid "04b19435-550d-5dbc-88d6-08f2bb6fba41")
    )
  )
  (entities
    (entity
      (name "tpl_root")
      (cell "root_cell" (uuid "9f66e37d-df75-5e24-b6cc-f02ee60d56dc"))
      (sheet "Own")
      (cluster "GRP")
      (uuid "37c9854d-ee0a-57fa-81ff-8561c9fede7e")
    )
    (entity
      (name "tpl_own")
      (cell "sub_a" (uuid "04b19435-550d-5dbc-88d6-08f2bb6fba41"))
      (sheet "Own")
      (cluster "GRP")
      (uuid "93dc40fa-129b-5b2a-938f-883a5dc69fe1")
    )
    (entity
      (name "tpl_foreign")
      (cell "sub_a" (uuid "04b19435-550d-5dbc-88d6-08f2bb6fba41"))
      (sheet "Own")
      (cluster "GRP")
      (uuid "4fe1ba9f-514b-5c5c-977e-42fb8a745252")
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
      (name "net_trace_001")
      (uuid "15a350e6-d148-555a-8ee2-f4a0bb9e2a56")
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
