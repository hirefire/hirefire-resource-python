from hirefire_resource.log import format_error

MESSAGES = [
    "redis://user:secret@host:6379/0",
    "redis://:p/ss@host:6379/0",
    "rediss://:pa@ss@host:6379",
    "amqp://guest:gu%40est@host//",
    "Error connecting to redis://default:tok3n@ec2-1.compute.amazonaws.com:6380",
    "no url host=h password=secret",
]


if __name__ == "__main__":
    for message in MESSAGES:
        print(format_error(Exception(message)))
