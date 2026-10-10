RQT = "rqt"
JQL = "jql"
JQS = "jqs"
CPU = "cpu"
WRK = "wrk"
JOB_QUEUE = (JQL, JQS)


def rqt(strategy: object) -> bool:
    return str(strategy) == RQT


def job_queue(strategy: object) -> bool:
    return str(strategy) in JOB_QUEUE
