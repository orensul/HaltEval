void id96(){
  int i = 0;
  int j = 0;
  while (i < 10) {
  retry:
    j++;
    goto retry;
  }
}