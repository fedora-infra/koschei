# Development config - local loop
config = dict(
    endpoints={
        "relay_outbound": ["tcp://127.0.0.1:4001"],
        "koschei": [
            "tcp://127.0.0.1:2004",
            "tcp://127.0.0.1:2005",
        ],
        "fedora-infrastructure": [
            "tcp://127.0.0.1:2004",
        ],
    },

    name="koschei",
    relay_inbound="tcp://127.0.0.1:2003",
    environment="prod",
    high_water_mark=0,
    io_threads=1,
    post_init_sleep=0.2,
    irc=[],
    zmq_enabled=True,
    zmq_strict=False,

    sign_messages=False,
    validate_messages=True,
)
