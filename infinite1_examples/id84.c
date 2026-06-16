void id84(int array[256]){
  unsigned int i = 1;
  while (i != 0) {
    array[i] = i;
    i = i << 1;
  }
}