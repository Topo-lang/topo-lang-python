import multiprocessing

def spawn_worker(task_id):
    p = multiprocessing.Process(target=lambda: task_id)
    p.start()
    p.join()
    return 0
