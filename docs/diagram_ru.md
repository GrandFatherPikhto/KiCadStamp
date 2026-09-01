graph TD
    CLI[kicadstamp_cli.py] --> Config[config/]
    CLI --> Adapter[kicad/adapter.py]
    CLI --> Validation[validation.py]
    CLI --> Planner[placement/planner.py]
    CLI --> Executor[placement/executor/batch_executor.py]
    CLI --> ViaRegistry[registry.PlacementRegistry]
    CLI --> TrackRegistry[registry.TrackRegistry]
    CLI --> Extract[template_extraction.py]
    CLI --> Undo[undo.py]
    CLI --> Constants[constants.py]

    Config --> Exceptions[exceptions.py]
    Config --> Includes[config/includes.py: include: (external cells:/chains:/etc.)]

    Validation --> Config
    Validation --> ComponentPool[placement/services/component_pool.py]
    Validation --> Exceptions
    Validation --> Adapter

    ViaRegistry --> Config
    ViaRegistry --> Adapter
    ViaRegistry --> Exceptions

    TrackRegistry --> Config
    TrackRegistry --> Adapter
    TrackRegistry --> Exceptions

    Extract --> Adapter
    Extract --> Config
    Extract --> Exceptions

    Undo --> Adapter
    Undo --> Exceptions

    NetResolution[net_resolution.py] --> Exceptions
    NetResolution --> Config (используется ClonePlacement)
    NetResolution --> Extract (parametrize_net)