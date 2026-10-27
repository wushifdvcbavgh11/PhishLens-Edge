import time,math
class BudgetStop(Exception):pass
class Budget:
 def __init__(self,enabled=True,seconds=5,max_embeddings=4,max_candidates=2,clock=time.perf_counter):
  self.enabled=enabled;self.clock=clock;self.start=clock();self.seconds=seconds;self.max_embeddings=max_embeddings;self.max_candidates=max_candidates;self.embeddings=0;self.candidates=0;self.observed_max=0
 def remaining(self):return self.seconds-(self.clock()-self.start)
 def check_time(self):
  now=self.clock();latest_start=self.start+self.seconds-max(.85,1.5*self.observed_max+.15)
  if self.enabled and (not math.isfinite(now) or now>latest_start):raise BudgetStop('INSUFFICIENT_REMAINING_TIME')
 def embedding(self):
  if self.enabled and self.embeddings>=self.max_embeddings:raise BudgetStop('EMBEDDING_CALL_CAP')
  self.check_time();self.embeddings+=1
 def candidate(self,candidate):
  if self.enabled and self.candidates>=self.max_candidates:raise BudgetStop('ALIGNMENT_CANDIDATE_CAP')
  self.check_time();self.candidates+=1
 def observe(self,seconds):self.observed_max=max(self.observed_max,seconds)
 def config(self):return {'enabled':self.enabled,'max_embeddings':self.max_embeddings,'max_alignment_candidates':self.max_candidates,'seconds':self.seconds,'remaining_reserve':'max(0.85, 1.5*max_observed_embedding_s+0.15)','not_a_cancellation_guarantee':True}
