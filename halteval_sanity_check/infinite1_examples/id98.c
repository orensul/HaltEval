void id98(){
  int i = 0;

retry:
  while (i < 10) {
    if (i == 5)
      goto retry;
    i++;
  }
}