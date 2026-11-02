"""Bounded retries only for Office's explicit temporary call rejection."""
import time

def retry_busy(call,attempts=8):
    for attempt in range(attempts):
        try:return call()
        except Exception as exc:
            code=getattr(exc,'hresult',None)
            if code is None and exc.args and isinstance(exc.args[0],int):code=exc.args[0]
            if code not in {-2147418111,-2147417846} or attempt==attempts-1:raise
            import pythoncom
            pythoncom.PumpWaitingMessages()
            time.sleep(min(.25*(attempt+1),1.0))
