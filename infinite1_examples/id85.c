void id85(int array[256], int len){
  unsigned int i = 1;
  while (i < len) {
    array[i] = i;
    i = i << 1;
  }
}