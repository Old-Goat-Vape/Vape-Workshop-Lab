document.querySelectorAll('time.batch-time[datetime]').forEach(time => {
  const date=new Date(time.dateTime);
  if (!Number.isNaN(date.getTime())) time.textContent=date.toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'});
});
