"""Parent-only bounded invalidation channel, never mounted in an N1 child."""
from pathlib import Path
import socket
import threading

from app.ai.proposals.codec import canonical
from .ipc import send_frame,receive_frame
from .records import decode,require,PortError


class InvalidationServer:
    """One bounded writer request. The disposition API remains parent local."""
    def __init__(self,path,authority,*,delivery='normal'):
        require(delivery in ('normal','drop_before_request','drop_after_fsync'))
        self.path,self.authority,self.delivery=str(path),authority,delivery
        self.socket=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.socket.bind(self.path);self.socket.listen(1);self.socket.settimeout(3)
        self.thread=threading.Thread(target=self.serve,daemon=True)
        self.received=False;self.durable_ack=None

    def start(self):
        self.thread.start();return self

    def serve(self):
        try:
            connection,_=self.socket.accept()
            with connection:
                connection.settimeout(3)
                if self.delivery=='drop_before_request':return
                meta,body=receive_frame(connection)
                require(not body and set(meta)=={'context','request'})
                context=decode('InvalidationContext',canonical(meta['context']))
                request=decode('InvalidationRequest',canonical(meta['request']))
                self.received=True
                self.durable_ack=self.authority.invalidate_v1(context,request,3)
                if self.delivery=='drop_after_fsync':return
                send_frame(connection,self.durable_ack.document())
        except (OSError,PortError):
            return

    def close(self):
        self.socket.close();self.thread.join(4)
        require(not self.thread.is_alive(),'OBSERVER_UNAVAILABLE')


class InvalidationClient:
    def __init__(self,path):self.path=str(path)

    def invalidate_v1(self,context,request,timeout=3):
        require(type(timeout) in (float,int) and 0<timeout<=30,'DEADLINE_EXCEEDED')
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
                connection.settimeout(min(3,timeout))
                connection.connect(self.path)
                send_frame(connection,dict(context=context.document(),request=request.document()))
                value,body=receive_frame(connection)
                require(not body)
                ack=decode('InvalidationAck',canonical(value))
                require((ack.operation_id,ack.operation_digest,ack.deployment_ref)==
                    (request.operation_id,request.operation_digest,request.deployment_ref),'COMMIT_UNKNOWN')
                return ack
        except (OSError,PortError):
            raise PortError('COMMIT_UNKNOWN') from None
