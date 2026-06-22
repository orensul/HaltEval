void id79(int array[256], int len){
  unsigned int i = 0;
  unsigned int j = 0;
  while (i < len) {
    j = (i & (~7));
    array[j] = i;
    i++;
  }
}