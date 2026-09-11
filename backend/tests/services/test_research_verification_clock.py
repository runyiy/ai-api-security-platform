from datetime import datetime,timedelta,timezone
import pytest
from app.services import research_verification_clock as vc
from app.schemas.research_intent import IntentError

NOW=datetime(2032,4,5,10,tzinfo=timezone.utc)


@pytest.mark.parametrize('seconds',[30,120,300])
@pytest.mark.parametrize('offset,ok',[(-1,True),(0,False),(1,False)])
@pytest.mark.parametrize('advance',['utc','monotonic','both'])
def test_half_open_boundary_on_both_clocks(monkeypatch,seconds,offset,ok,advance):
    at=[NOW];ns=[5_000_000_000];monkeypatch.setattr(vc,'monotonic_ns',lambda:ns[0])
    clock=vc.Clock(lambda:at[0]);mark=clock.mark()
    vc.check_mark(mark,clock,seconds=seconds,end=NOW+timedelta(seconds=seconds))
    if advance in ('utc','both'):at[0]+=timedelta(seconds=seconds,microseconds=offset)
    if advance in ('monotonic','both'):ns[0]+=seconds*1_000_000_000+offset*1000
    if ok:clock()
    else:
        with pytest.raises(IntentError,match='expired'):clock()


@pytest.mark.parametrize('change',['domain','future','negative','backwards_utc','backwards_monotonic'])
def test_impossible_or_untrusted_clock(monkeypatch,change):
    ns=[10];at=[NOW];monkeypatch.setattr(vc,'monotonic_ns',lambda:ns[0])
    clock=vc.Clock(lambda:at[0]);mark=clock.mark()
    if change=='domain':mark['clock_domain']='f'*64
    elif change=='future':mark['at']=(NOW+timedelta(seconds=1)).isoformat()
    elif change=='negative':mark['monotonic_ns']=-1
    elif change=='backwards_utc':at[0]-=timedelta(microseconds=1)
    else:ns[0]-=1
    with pytest.raises(IntentError):vc.check_mark(mark,clock,seconds=120)


@pytest.mark.parametrize('seconds',[30,120,300])
@pytest.mark.parametrize('advance',['utc','monotonic'])
def test_expiry_poison_is_irreversible_and_records_only_affected_consumers(monkeypatch,seconds,advance):
    from app.services import research_verification_fault as fault
    at=[NOW];ns=[10_000_000_000];recorded=[]
    monkeypatch.setattr(vc,'monotonic_ns',lambda:ns[0])
    monkeypatch.setattr(fault,'record',lambda *args:recorded.append(args[-1]['digest']))
    clock=vc.Clock(lambda:at[0]);mark=clock.mark()
    business=dict(number=1,version=1,digest='b'*64)
    health=dict(number=2,version=1,digest='a'*64)
    clock.bind_intent(None,1,1,business)
    with clock.dependency():
        clock.bind_intent(None,1,1,health)
        vc.check_mark(mark,clock,seconds=120)
    # Pair/intent windows only belong to the primary consumer. For health the
    # registered nested constraint retains both owners after leaving its scope.
    vc.check_mark(mark,clock,seconds=seconds)
    if advance=='utc':at[0]+=timedelta(seconds=min(seconds,120))
    else:ns[0]+=min(seconds,120)*1_000_000_000
    with pytest.raises(IntentError,match='expired'):clock()
    assert set(recorded)==({'b'*64} if seconds==30 else {'a'*64,'b'*64})
    at[0]=NOW;ns[0]+=1
    with pytest.raises(IntentError,match='clock_invalidated'):clock()


def test_core_windows_share_existing_constraint_limit(monkeypatch):
    monkeypatch.setattr(vc,'monotonic_ns',lambda:10)
    clock=vc.Clock(NOW);mark=clock.mark()
    for seconds in range(1,33):
        clock.watch_window(NOW,NOW+timedelta(seconds=seconds))
        vc.check_mark(mark,clock,seconds=seconds)
    # Refreshing an existing constraint is allowed; neither kind can add a 65th.
    clock.watch_window(NOW,NOW+timedelta(seconds=1))
    vc.check_mark(mark,clock,seconds=1)
    with pytest.raises(IntentError,match='verification_clock_limit'):
        clock.watch_window(NOW,NOW+timedelta(seconds=33))
    with pytest.raises(IntentError,match='verification_clock_limit'):
        vc.check_mark(mark,clock,seconds=33)
