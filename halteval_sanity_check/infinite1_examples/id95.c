void id95(){
  int i = 0;

  while (i < 10) {
  retry:
    if (i == 5)
      goto retry;
    i++;
  }
}