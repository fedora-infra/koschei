_message_queue = None
mock_published = None

def mock_init():
    global _message_queue
    global mock_published
    _message_queue = []
    mock_published = []

def mock_add_message(name='', topic='', endpoint='', msg=''):
    _message_queue.append((name, endpoint, topic, msg))

def mock_verify_empty():
    if _message_queue:
        raise AssertionError("Messages remaining in fedmsg queue")

def tail_messages():
    global _message_queue
    for item in _message_queue:
        yield item
    _message_queue = []

def publish(topic, modname, msg):
    mock_published.append((topic, modname, msg))
