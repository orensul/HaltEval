void id97(){
  int i = 0;

retry:
  while (i < 10) {
    if (i == 6)
      goto retry;
    i++;
  }
}