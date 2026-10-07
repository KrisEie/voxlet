"""Keep spoken reference content while removing quiet edges, never internal pauses."""
import numpy as np
import soundfile as sf


def trim_quiet_edges(audio, rate, padding=.15):
    x=np.asarray(audio,dtype=np.float32)
    if x.ndim>1:x=x[:,int(np.argmax(np.mean(x*x,axis=0)))]
    if not len(x):return x,0,0
    frame=max(1,round(rate*.02))
    # Zero-pad only for measuring; exported samples come from the original array.
    padded=np.pad(x,(0,(-len(x))%frame))
    rms=np.sqrt(np.mean(padded.reshape(-1,frame)**2,axis=1))
    threshold=max(.001,min(.008,float(np.max(rms))*.02))
    active=np.flatnonzero(rms>threshold)
    if not len(active):return x,0,0
    margin=round(rate*padding)
    start=max(0,int(active[0])*frame-margin)
    end=min(len(x),(int(active[-1])+1)*frame+margin)
    # Trimming cannot turn an otherwise valid reference into a tiny fragment.
    if end-start<rate*3:return x,0,0
    return x[start:end].copy(),start/rate,(len(x)-end)/rate


def prepare_reference(source,destination):
    data,rate=sf.read(source,dtype='float32',always_2d=True)
    clean,leading,trailing=trim_quiet_edges(data,rate)
    sf.write(destination,clean,rate,subtype='PCM_16')
    return {'leading_trim_seconds':leading,'trailing_trim_seconds':trailing,'seconds':len(clean)/rate}
