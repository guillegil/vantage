"""The ingestion seam (design §3, §11) -- the `EventSink` Protocol producers
write through, plus the shared validate-before-dispatch enforcement every
concrete sink must apply.
"""
