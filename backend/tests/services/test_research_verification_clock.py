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
