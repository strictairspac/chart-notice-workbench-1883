from redis.exceptions import RedisError

from src.config.resources import cache, engine
from src.services import archives, cases, reviews, runs


def wake():
    try:
        cache.lpush("workbench:wake", "1")
        cache.ltrim("workbench:wake", 0, 99)
    except RedisError:
        # The database holds the durable queue. The worker also polls it.
        pass


def new_case(data, user):
    with engine.begin() as db:
        return cases.create(db, data, user["username"])


def new_run(case_id, data, user):
    with engine.begin() as db:
        run = runs.enqueue(db, case_id, data.parameter, user["username"])
    wake()
    return run


def new_review(run_id, data, user):
    with engine.begin() as db:
        return reviews.sign(db, run_id, data, user["username"])


def new_archive(data, user):
    with engine.begin() as db:
        import_id = archives.start(db, data, user["username"])
        view = archives.view(db, import_id)
    if view["state"] != "done":
        wake()
    return view


def resume_archive(import_id, user):
    with engine.begin() as db:
        archives.resume(db, import_id, user["username"])
        view = archives.view(db, import_id)
    if view["state"] != "done":
        wake()
    return view


def resolve_receipt(receipt_id, decision, user):
    with engine.begin() as db:
        return dict(archives.resolve(db, receipt_id, decision, user["username"]))
